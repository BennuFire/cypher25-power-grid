"""Load data/import/transmission/*.csv (PyPSA-Eur OSM grid + powerplantmatching) into Neo4j.

Every node gets the :PyPSA source label. CIM-style model (see cim.py): lines, HVDC links,
converters and plants are nodes wired with [:CONNECTS] through ConnectionNodes to buses;
transformers own TransformerWindings through [:HAS_WINDING] and belong to substations
through (:Substation)-[:HAS_TRANSFORMER]->(:Transformer).
Credentials come from .env — see db.py. Idempotent: everything is MERGEd on its id.
"""
import pandas as pd

from cim import SCHEMA, load_topology
from db import ROOT, Graph, records

DATA = ROOT / "data" / "import" / "transmission"
POINT = "point({longitude: toFloat(row.lon), latitude: toFloat(row.lat)})"

# (csv file, cypher) — each row is available as `row`
STEPS = [
    ("countries", """
        MERGE (c:Country {code: row.code}) SET c:PyPSA, c.name = row.name"""),
    ("substations", f"""
        MERGE (s:Substation {{id: row.id}})
        SET s:PyPSA, s.name = row.name, s.country = row.country,
            s.maxVoltage = toInteger(row.max_voltage), s.busCount = toInteger(row.bus_count),
            s.nameSource = row.name_source, s.osmName = row.osm_name,
            s.nearCity = row.near_city, s.nearCityKm = toFloat(row.near_city_km),
            s.location = {POINT}, s.mergeGroup = row.merge_group, s.osmIds = split(row.osm_ids, ';'),
            s.footprint = [p IN split(row.footprint, ';') |
                           point({{longitude: toFloat(split(p, ' ')[0]), latitude: toFloat(split(p, ' ')[1])}})],
            s.footprintKind = row.footprint_kind, s.areaM2 = toFloat(row.area_m2)
        WITH s, row
        MATCH (c:Country {{code: row.country}})
        MERGE (s)-[:IN_COUNTRY]->(c)"""),
    ("buses", f"""
        MERGE (b:Bus {{id: row.id}})
        SET b:PyPSA, b.voltage = toInteger(row.voltage), b.dc = row.dc = 'True',
            b.country = row.country, b.location = {POINT}, b.osmIds = split(row.osm_ids, ';')
        WITH b, row
        MATCH (s:Substation {{id: row.substation_id}})
        MERGE (s)-[:HAS_BUS]->(b)"""),
    ("lines", """
        MERGE (l:Line {id: row.id})
        SET l:PyPSA, l.voltage = toInteger(row.voltage), l.circuits = toInteger(row.circuits),
            l.capacityMVA = toFloat(row.capacity_mva), l.iNomKA = toFloat(row.i_nom_ka),
            l.r = toFloat(row.r), l.x = toFloat(row.x), l.b = toFloat(row.b),
            l.lengthKm = toFloat(row.length_km), l.underground = row.underground = 'True',
            l.crossBorder = row.cross_border = 'True', l.type = row.type,
            l.osmIds = split(row.osm_ids, ';')"""),
    ("hvdc", """
        MERGE (l:Line {id: row.id})
        SET l:PyPSA:HVDC, l.voltage = toInteger(row.voltage),
            l.capacityMW = toFloat(row.capacity_mw), l.lengthKm = toFloat(row.length_km),
            l.underground = row.underground = 'True', l.crossBorder = row.cross_border = 'True',
            l.osmIds = split(row.osm_ids, ';')"""),
    ("transformers", f"""
        MERGE (t:Transformer {{id: row.id}})
        SET t:PyPSA, t.voltageFrom = toInteger(row.voltage_from),
            t.voltageTo = toInteger(row.voltage_to), t.capacityMVA = toFloat(row.capacity_mva),
            t.location = {POINT}, t.spansSubstations = row.spans_substations = 'True',
            t.mergeGroup = row.merge_group"""),
    ("converters", """
        MERGE (c:Converter {id: row.id})
        SET c:PyPSA, c.voltage = toInteger(row.voltage), c.capacityMW = toFloat(row.capacity_mw)"""),
    ("plants", f"""
        MERGE (p:PowerPlant {{id: row.id}})
        SET p:PyPSA, p.name = row.name, p.fuel = row.fuel, p.technology = row.technology,
            p.set = row.set, p.country = row.country, p.capacityMW = toFloat(row.capacity_mw),
            p.yearIn = toInteger(row.year_in), p.location = {POINT},
            p.busDistanceKm = toFloat(row.bus_km)"""),
]


def main():
    with Graph() as g:
        for stmt in SCHEMA:
            g.run(stmt)
        for name, cypher in STEPS:
            df = pd.read_csv(DATA / f"{name}.csv", dtype=str, keep_default_na=False)
            g.load(cypher, records(df.replace("", float("nan"))))
            print(f"{name:13s} {len(df):>6} rows")
        print(f"connects      {load_topology(g, DATA, 'PyPSA'):>6} rows")
        # A transformer belongs to the substation(s) of the buses its windings connect to.
        # 258 transformers join two PyPSA substations 22-121 m apart (same site, mapped twice
        # in OSM) and get both; see Substation.mergeGroup / Transformer.spansSubstations.
        g.run("""
            MATCH (s:Substation)-[:HAS_BUS]->(:Bus)<-[:CONNECTS]-(:ConnectionNode)
                  <-[:CONNECTS]-(:TransformerWinding)<-[:HAS_WINDING]-(t:Transformer:PyPSA)
            MERGE (s)-[:HAS_TRANSFORMER]->(t)""")
        print("graph:", g.counts())


if __name__ == "__main__":
    main()
