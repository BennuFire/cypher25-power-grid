"""Benchmark the restoration questions with and without ACYCLIC / allReduce.

Scenarios (see queries/):
  q1  Trafo1 (110/10 kV) fails, Trafo2 re-supplies          (q1_restoration.cypher)
  q2  a 10 kV cable fails, any 110/10 kV transformer re-supplies (q2_cable_fault.cypher)

Every variant answers the same question — which lost supply units can be reached by closing
exactly one open switch — so their results must match. Only the path search differs:

  V1  ACYCLIC + QPP + allReduce      prune paths with a 2nd open switch while traversing
  V2  QPP + allReduce, no ACYCLIC
  V3  ACYCLIC + QPP                  count open switches only after the path is complete
  V4  QPP only                       neither
  V5  old variable-length syntax     -[:CONNECTS|HAS_WINDING*]-, every check afterwards

Fast variants: one warm-up, then the median of 3 timed runs (no PROFILE), plus one PROFILE run
for db hits. Slow variants get one run with a time limit. Aura Free is shared, so treat times
as indicative and db hits as the stable metric.
Usage: python bench_restoration.py [q1|q2 ...]   -> blog/results/restoration_<scenario>.csv
"""
import statistics
import sys
import time

import pandas as pd
from neo4j import Query

from db import ROOT, Graph

TIME_LIMIT_S = 120
RUNS = 3
UNIT = "(u:TransformerWinding AND u.side = 'primary') OR u:Consumer"
CLOSED_LEVEL = "(NOT n:Switch OR n.closed) AND coalesce(n.voltage, w.voltage) = w.voltage"

# Each scenario: parameters, a prefix that binds `lost` (units to re-supply) and a source
# winding `w`, and the condition every step must meet besides staying at w's voltage level.
SCENARIOS = {
    "q1": {
        "title": "Trafo1 fails, Trafo2 re-supplies",
        "params": {"failed": "sb-trafo-133", "backup": "sb-trafo-134"},
        "prefix": f"""
MATCH (:Transformer {{id: $failed}})-[:HAS_WINDING]->(w:TransformerWinding {{side: 'secondary'}})
MATCH ACYCLIC (w) (()-[:CONNECTS|HAS_WINDING]-(n WHERE {CLOSED_LEVEL}))+ (u) WHERE {UNIT}
WITH collect(DISTINCT u) AS lost
MATCH (:Transformer {{id: $backup}})-[:HAS_WINDING]->(w:TransformerWinding {{side: 'secondary'}})
""",
        "avoid": "NOT (n.id = $failed OR n.id STARTS WITH $failed + ':')",
    },
    "q2": {
        "title": "10 kV cable fault (largest: MV3.101 Line 67, 1,622 customers)",
        "params": {"faulted": "MV3.101 Line 67"},
        "prefix": f"""
MATCH (f:Line:SimBench {{name: $faulted}})
MATCH (:Transformer)-[:HAS_WINDING]->(w:TransformerWinding {{side: 'secondary'}}) WHERE w.voltage = f.voltage
WITH f, collect(w) AS sources
CALL (f, sources) {{
  UNWIND sources AS w
  MATCH ACYCLIC (w) (()-[:CONNECTS|HAS_WINDING]-(n WHERE {CLOSED_LEVEL}))+ (u) WHERE {UNIT}
  RETURN collect(DISTINCT u) AS before
}}
CALL (f, sources) {{
  UNWIND sources AS w
  MATCH ACYCLIC (w) (()-[:CONNECTS|HAS_WINDING]-(n WHERE {CLOSED_LEVEL} AND n <> f))+ (u) WHERE {UNIT}
  RETURN collect(DISTINCT u) AS after
}}
WITH f, sources, [u IN before WHERE NOT u IN after] AS lost
UNWIND sources AS w
""",
        "avoid": "n <> f",
    },
}

OPEN1 = ("allReduce(open = 0, x IN n | open + CASE WHEN x:Switch AND NOT x.closed THEN 1 ELSE 0 END,"
         " open <= 1)")
RESULT = """
WITH u, [x IN nodes(p) WHERE x:Switch AND NOT x.closed] AS opened
WHERE size(opened) = 1
RETURN count(DISTINCT opened[0]) AS switches, count(DISTINCT u) AS units"""


def variants(avoid):
    step = (f"()-[:CONNECTS|HAS_WINDING]-(n WHERE coalesce(n.voltage, w.voltage) = w.voltage "
            f"AND {avoid})")
    every = f"all(n IN nodes(p) WHERE coalesce(n.voltage, w.voltage) = w.voltage AND {avoid})"
    return {
        "V1 ACYCLIC + QPP + allReduce": f"MATCH p = ACYCLIC (w) ({step})+ (u) WHERE u IN lost AND {OPEN1}",
        "V2 QPP + allReduce (no ACYCLIC)": f"MATCH p = (w) ({step})+ (u) WHERE u IN lost AND {OPEN1}",
        "V3 ACYCLIC + QPP (no allReduce)": f"MATCH p = ACYCLIC (w) ({step})+ (u) WHERE u IN lost",
        "V4 QPP only": f"MATCH p = (w) ({step})+ (u) WHERE u IN lost",
        "V5 old variable-length syntax":
            f"MATCH p = (w)-[:CONNECTS|HAS_WINDING*]-(u) WHERE u IN lost AND {every}",
    }


def failure(e):
    """Why a query stopped: Aura's time limit, its 2.2 GiB transaction memory, or else."""
    if "TransactionTimedOut" in str(e):
        return f"time limit ({TIME_LIMIT_S}s)"
    if "MemoryPoolOutOfMemory" in str(e):
        return "memory limit (2.2 GiB per transaction on Aura Free)"
    return type(e).__name__


def db_hits(plan):
    return plan.get("dbHits", 0) + sum(db_hits(c) for c in plan.get("children", []))


def execute(g, scenario, match, profile=False):
    text = ("PROFILE " if profile else "") + "CYPHER 25 " + scenario["prefix"] + match + RESULT
    t = time.perf_counter()
    with g.driver.session(database=g.database) as s:
        res = s.run(Query(text, timeout=TIME_LIMIT_S), **scenario["params"])
        row = res.single().data()
        summary = res.consume()
    return row, time.perf_counter() - t, (db_hits(summary.profile) if profile else None)


def bench(g, name, scenario):
    print(f"\n{name}: {scenario['title']}")
    rows = []
    for label, match in variants(scenario["avoid"]).items():
        try:
            row, first_s, _ = execute(g, scenario, match)  # warm-up, or the only run if slow
            if first_s < 30:
                times = [execute(g, scenario, match)[1] for _ in range(RUNS)]
                hits = execute(g, scenario, match, profile=True)[2]
                seconds, status = statistics.median(times), "ok"
            else:
                seconds, hits, status = first_s, None, "ok (single run)"
        except Exception as e:  # time limit, memory limit or dropped connection
            row, seconds, hits = {}, None, None
            status = f"did not finish: {failure(e)}"
        rows.append({"variant": label, **row, "seconds": seconds and round(seconds, 2),
                     "db_hits": hits, "status": status})
        print(" ", rows[-1])
    return pd.DataFrame(rows)


def main():
    out = ROOT / "blog" / "results"
    out.mkdir(parents=True, exist_ok=True)
    with Graph() as g:
        for name in sys.argv[1:] or SCENARIOS:
            bench(g, name, SCENARIOS[name]).to_csv(out / f"restoration_{name}.csv", index=False)


if __name__ == "__main__":
    main()
