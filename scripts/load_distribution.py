"""Load data/import/distribution/*.csv (SimBench urban MV/LV) into the same Neo4j database.

Every node gets the :SimBench source label; buses and windings also get :HV, :MV or :LV.
CIM-style model, see cim.py. Run after load_transmission.py — it creates the shared
constraints and the PyPSA bus the synthetic supply transformer attaches to.
"""
import pandas as pd

from cim import SCHEMA, SET_LEVEL, load_topology
from db import ROOT, Graph, records

DATA = ROOT / "data" / "import" / "distribution"
POINT = "point({longitude: toFloat(row.lon), latitude: toFloat(row.lat)})"

STEPS = [
    ("buses", f"""
        MERGE (n:Bus {{id: row.id}})
        SET n:SimBench, n.name = row.name, n.voltage = toFloat(row.voltage), n.level = row.level,
            n.kind = row.kind, n.subnet = row.subnet, n.isSource = row.is_source = 'True',
            n.location = {POINT}
        WITH n, row
        {SET_LEVEL}"""),
    ("switches", f"""
        MERGE (s:Switch {{id: row.id}})
        SET s:SimBench, s.name = row.name, s.type = row.type, s.closed = row.closed = 'True',
            s.switches = row.switches, s.subnet = row.subnet, s.voltage = toFloat(row.voltage),
            s.location = {POINT}"""),
    ("lines", """
        MERGE (l:Line {id: row.id})
        SET l:SimBench, l.name = row.name, l.stdType = row.std_type,
            l.lengthKm = toFloat(row.length_km), l.maxIKA = toFloat(row.max_i_ka),
            l.kind = row.kind, l.subnet = row.subnet, l.voltage = toFloat(row.voltage),
            l.rOhmPerKm = toFloat(row.r_ohm_per_km), l.xOhmPerKm = toFloat(row.x_ohm_per_km),
            l.cNfPerKm = toFloat(row.c_nf_per_km)"""),
    ("transformers", f"""
        MERGE (t:Transformer {{id: row.id}})
        SET t:SimBench, t.name = row.name, t.stdType = row.std_type,
            t.capacityMVA = toFloat(row.capacity_mva), t.voltageFrom = toFloat(row.voltage_from),
            t.voltageTo = toFloat(row.voltage_to), t.subnet = row.subnet, t.location = {POINT}"""),
    ("consumers", f"""
        MERGE (c:Consumer {{id: row.id}})
        SET c:SimBench, c.name = row.name, c.pMW = toFloat(row.p_mw), c.profile = row.profile,
            c.segment = row.segment, c.location = {POINT}"""),
    ("generators", f"""
        MERGE (g:Generator {{id: row.id}})
        SET g:SimBench, g.name = row.name, g.type = row.type, g.pMW = toFloat(row.p_mw),
            g.profile = row.profile, g.location = {POINT}"""),
    ("supply_tie", f"""
        MERGE (t:Transformer {{id: row.id}})
        SET t:SimBench:Synthetic, t.name = 'Synthetic supply transformer',
            t.voltageFrom = toFloat(row.voltage_from), t.voltageTo = toFloat(row.voltage_to),
            t.distanceKm = toFloat(row.distance_km), t.location = {POINT},
            t.note = 'Not real: ties the SimBench grid to the PyPSA grid for cross-layer queries'"""),
]


def main():
    with Graph() as g:
        for stmt in SCHEMA:
            g.run(stmt)
        for name, cypher in STEPS:
            df = pd.read_csv(DATA / f"{name}.csv", dtype=str, keep_default_na=False)
            g.load(cypher, records(df.replace("", float("nan"))))
            print(f"{name:13s} {len(df):>6} rows")
        print(f"connects      {load_topology(g, DATA, 'SimBench'):>6} rows")
        print("graph:", g.counts())


if __name__ == "__main__":
    main()
