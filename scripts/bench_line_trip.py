"""Benchmark Q3 (N-1: a 400 kV line trips, which routes connect its ends?) by path mode.

  V1  ACYCLIC          routes never revisit a bus — loops are cut while searching
  V2  post-filter      match every route, then keep those without a repeated node
                       (the pre-path-mode way; same answer as V1)
  V3  TRAIL            no relationship twice, but buses may repeat: counts "out on one parallel
                       circuit, straight back on the other" as a route — a different answer
At 12 hops all three run out of memory, so two ways to bound the search are added there:
  V4  ACYCLIC + 400 km budget       allReduce on the running route length
  V5  post-filter + 400 km budget
  V6  SHORTEST 10 ACYCLIC           the 10 shortest loop-free routes only
  V7  SHORTEST 10                   no path mode: shortest routes may revisit a bus

There is no "old variable-length" variant: the 4-relationship CIM hop with its terminal check
cannot be written as -[*]-.
Fast variants: one warm-up, then the median of 3 timed runs (no PROFILE), plus one PROFILE run
for db hits. Slow variants get one run with a time limit. Aura Free is shared, so treat times
as indicative and db hits as the stable metric.  -> blog/results/line_trip.csv
"""
import statistics
import time

import pandas as pd
from neo4j import Query

from db import ROOT, Graph

PARAMS = {"tripped": "relation/6554523-400"}   # Villejust – Cirolliers 400 kV
HOPS = (8, 10, 12)
TIME_LIMIT_S = 120
RUNS = 3

ENDS = """
MATCH (x:Line {id: $tripped})-[:CONNECTS {terminal: 1}]->(:ConnectionNode)-[:CONNECTS]->(a:Bus),
      (x)-[:CONNECTS {terminal: 2}]->(:ConnectionNode)-[:CONNECTS]->(b:Bus)
"""
HOP = ("((:Bus)<-[:CONNECTS]-(:ConnectionNode)<-[t1:CONNECTS]-(l:Line WHERE l <> x)"
       "-[t2:CONNECTS]->(:ConnectionNode)-[:CONNECTS]->(:Bus) WHERE t1.terminal <> t2.terminal)")
RESULT = (" RETURN count(p) AS routes, count(DISTINCT [n IN nodes(p) WHERE n:Bus]) AS corridors,"
          " sum(CASE WHEN size([n IN nodes(p) WHERE n:Bus])"
          " > size(coll.distinct([n IN nodes(p) WHERE n:Bus])) THEN 1 ELSE 0 END) AS routesRevisitingABus")
BUDGET = "allReduce(km = 0.0, r IN l | km + r.lengthKm, km <= 400)"
NO_REPEAT = "size(nodes(p)) = size(coll.distinct(nodes(p)))"


def variants(k):
    v = {
        "V1 ACYCLIC": f"MATCH p = ACYCLIC (a) {HOP}{{1,{k}}} (b)",
        "V2 post-filter": f"MATCH p = (a) {HOP}{{1,{k}}} (b) WHERE {NO_REPEAT}",
        "V3 TRAIL": f"MATCH p = TRAIL (a) {HOP}{{1,{k}}} (b)",
    }
    if k == max(HOPS):
        v |= {
            "V4 ACYCLIC + 400 km budget": f"MATCH p = ACYCLIC (a) {HOP}{{1,{k}}} (b) WHERE {BUDGET}",
            "V5 post-filter + 400 km budget":
                f"MATCH p = (a) {HOP}{{1,{k}}} (b) WHERE {NO_REPEAT} AND {BUDGET}",
            "V6 SHORTEST 10 ACYCLIC": f"MATCH p = SHORTEST 10 ACYCLIC (a) {HOP}{{1,{k}}} (b)",
            "V7 SHORTEST 10": f"MATCH p = SHORTEST 10 (a) {HOP}{{1,{k}}} (b)",
        }
    return v


def failure(e):
    """Why a query stopped: Aura's time limit, its 2.2 GiB transaction memory, or else."""
    if "TransactionTimedOut" in str(e):
        return f"time limit ({TIME_LIMIT_S}s)"
    if "MemoryPoolOutOfMemory" in str(e):
        return "memory limit (2.2 GiB per transaction on Aura Free)"
    return type(e).__name__


def db_hits(plan):
    return plan.get("dbHits", 0) + sum(db_hits(c) for c in plan.get("children", []))


def execute(g, match, profile=False):
    text = ("PROFILE " if profile else "") + "CYPHER 25 " + ENDS + match + RESULT
    t = time.perf_counter()
    with g.driver.session(database=g.database) as s:
        res = s.run(Query(text, timeout=TIME_LIMIT_S), **PARAMS)
        row = res.single().data()
        summary = res.consume()
    return row, time.perf_counter() - t, (db_hits(summary.profile) if profile else None)


def main():
    out = ROOT / "blog" / "results"
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    with Graph() as g:
        for k in HOPS:
            for label, match in variants(k).items():
                try:
                    row, first_s, _ = execute(g, match)  # warm-up, or the only run if slow
                    if first_s < 30:
                        times = [execute(g, match)[1] for _ in range(RUNS)]
                        hits = execute(g, match, profile=True)[2]
                        seconds, status = statistics.median(times), "ok"
                    else:
                        seconds, hits, status = first_s, None, "ok (single run)"
                except Exception as e:  # time limit, memory limit or dropped connection
                    row, seconds, hits = {}, None, None
                    status = f"did not finish: {failure(e)}"
                rows.append({"max_hops": k, "variant": label, **row,
                             "seconds": seconds and round(seconds, 2), "db_hits": hits,
                             "status": status})
                print(rows[-1])
    pd.DataFrame(rows).to_csv(out / "line_trip.csv", index=False)


if __name__ == "__main__":
    main()
