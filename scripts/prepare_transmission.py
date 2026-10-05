"""Clean the PyPSA-Eur OSM network + powerplantmatching into Neo4j import CSVs.

Inputs  (data/raw):    transmission/*.csv (Zenodo 10.5281/zenodo.12799201 v0.7),
                       transmission/powerplants.csv (powerplantmatching),
                       geonames/cities1000.txt, geonames/countryInfo.txt,
                       osm/substation_tags.csv, osm/substation_geometry.csv (optional, from
                       fetch_osm.py: real names and footprints)
Outputs (data/import/transmission): substations, buses, countries, lines, hvdc,
                       transformers, converters, plants + CIM topology (connection_nodes,
                       windings, connects) — see cim.py
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd

from cim import Topology
from geo import convex_hull, distances, nearest, ring_area_m2

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
OUT = ROOT / "data" / "import" / "transmission"
OUT.mkdir(parents=True, exist_ok=True)

MIN_PLANT_MW = 10        # smaller units connect to distribution, not 220 kV+
MAX_PLANT_KM = 100       # drop plants with no transmission bus nearby (islands etc.)
MAX_OFFSHORE_KM = 250    # offshore wind farms sit far from the onshore landing bus
CURRENT_YEAR = 2026
BIG_CITY_POP = 200_000
BIG_CITY_KM = 20


def read_pypsa(name):
    # PyPSA-Eur writes WKT geometry quoted with single quotes
    df = pd.read_csv(RAW / "transmission" / f"{name}.csv", quotechar="'")
    return df.loc[:, ~df.columns.str.startswith("Unnamed")].drop(columns="geometry")


def flag(s):
    return s.map({"t": True, "f": False})


# --- countries ---------------------------------------------------------------
ci_rows = [ln.split("\t") for ln in (RAW / "geonames" / "countryInfo.txt")
           .read_text(encoding="utf-8").splitlines() if ln and not ln.startswith("#")]
iso_name = {r[0]: r[4] for r in ci_rows}
# powerplantmatching uses English country names; GeoNames spells one of them differently
name_iso = {v: k for k, v in iso_name.items()} | {"Netherlands": "NL"}  # GeoNames: "The Netherlands"

# --- buses & substations -----------------------------------------------------
buses = read_pypsa("buses")
buses["substation_id"] = buses.bus_id.str.rsplit("-", n=1).str[0].where(buses.dc == "f",
                                                                         buses.bus_id)
# `tags` = the OSM objects a bus/line was built from (PyPSA merges substations within 500 m,
# so the id names only one of them). Kept as ';'-separated `osm_ids` for provenance.
buses = buses.rename(columns={"bus_id": "id", "x": "lon", "y": "lat", "tags": "osm_ids"})
buses["dc"] = flag(buses.dc)
buses = buses[["id", "substation_id", "voltage", "dc", "country", "lon", "lat", "osm_ids"]]

subs = (buses.groupby("substation_id")
        .agg(lon=("lon", "mean"), lat=("lat", "mean"), country=("country", "first"),
             max_voltage=("voltage", "max"), bus_count=("id", "size"),
             osm_ids=("osm_ids", lambda t: ";".join(sorted(set(";".join(t).split(";"))))))
        .reset_index().rename(columns={"substation_id": "id"}))

# --- substation names: real OSM name, else generated from the nearest city --------
# OSM names come from the power=substation objects behind a substation (cached by
# fetch_osm.py). virtual_* substations are built from power lines and have none.
osm_tags = RAW / "osm" / "substation_tags.csv"
if osm_tags.exists():
    t = pd.read_csv(osm_tags)
    sub_names = t[(t.power == "substation") & t.name.notna()].set_index("osm_id").name
    names = subs.osm_ids.str.split(";").map(
        lambda ids: " / ".join(sorted({sub_names[i] for i in ids if i in sub_names})))
    subs["osm_name"] = names.where(names != "")
else:
    print("no OSM tag cache (run fetch_osm.py): all names generated from cities")
    subs["osm_name"] = None

# City fallback: the largest big city when one is close ("Paris" beats "Nanterre"), else the nearest
# town. Same country only, and no city districts (GeoNames PPLX, e.g. "Paris 15 Vaugirard").
# cities1000 (places >= 1,000 people): cities15000 left sparse regions with towns 100+ km away
cities = pd.read_csv(RAW / "geonames" / "cities1000.txt", sep="\t", header=None,
                     usecols=[1, 4, 5, 7, 8, 14],
                     names=["name", "lat", "lon", "fcode", "cc", "pop"], keep_default_na=False)
cities = cities[cities.fcode != "PPLX"]
subs["near_city"], subs["near_city_km"] = None, np.nan
for cc, idx in subs.groupby("country").groups.items():
    local = cities[cities.cc == cc]
    big = local[local["pop"] >= BIG_CITY_POP]
    lat, lon = subs.loc[idx, "lat"].values, subs.loc[idx, "lon"].values
    ci_idx, ci_km = nearest(lat, lon, local.lat.values, local.lon.values, chunk=200)
    city, km = local.name.values[ci_idx], ci_km
    if len(big):
        # The LARGEST big city within reach, not the nearest: GeoNames lists some districts
        # as places (Madrid's Ciudad Lineal, 228k people), which would otherwise beat Madrid
        d = distances(lat, lon, big.lat.values, big.lon.values)
        within = d <= BIG_CITY_KM
        bi_idx = np.where(within, big["pop"].values, -1).argmax(axis=1)
        use_big = within.any(axis=1)
        city = np.where(use_big, big.name.values[bi_idx], city)
        km = np.where(use_big, d[np.arange(len(d)), bi_idx], km)
    subs.loc[idx, "near_city"], subs.loc[idx, "near_city_km"] = city, km.round(1)

subs["name_source"] = np.where(subs.osm_name.notna(), "osm", "city")
base = subs.osm_name.fillna(subs.near_city)
# Unique names: duplicates get " #2", " #3", ... (OSM names first, then by distance to city)
subs = subs.assign(base=base).sort_values(["base", "name_source", "near_city_km", "id"],
                                          ascending=[True, False, True, True])
rank = subs.groupby("base").cumcount() + 1
subs["name"] = subs.base + np.where(rank > 1, " #" + rank.astype(str), "")
subs = subs.drop(columns="base")

# --- substation footprints from OSM outlines (cached by fetch_osm.py) ----------------
# One closed OSM outline is kept as is. Several OSM objects (sites PyPSA merged) or a
# relation whose outer edge comes in pieces become their convex hull: one clean shape that
# fits in a single list of points. `location` stays PyPSA's point (inside the outline 94%).
osm_geom = RAW / "osm" / "substation_geometry.csv"
subs["footprint"], subs["footprint_kind"], subs["area_m2"] = None, None, np.nan
if osm_geom.exists():
    outline = pd.read_csv(osm_geom).set_index("osm_id").parts.map(json.loads)
    for i, ids in zip(subs.index, subs.osm_ids.str.split(";")):
        rings = [r for x in ids if x in outline for r in outline[x] if len(r) >= 2]
        if not rings:
            continue
        if len(rings) == 1 and len(rings[0]) >= 4 and rings[0][0] == rings[0][-1]:
            ring, kind = rings[0], "outline"
        else:
            ring, kind = convex_hull([pt for r in rings for pt in r]), "convexHull"
        if len(ring) < 4:  # degenerate (fewer than 3 distinct points)
            continue
        subs.at[i, "footprint"] = ";".join(f"{lon} {lat}" for lon, lat in ring)
        subs.at[i, "footprint_kind"] = kind
        subs.at[i, "area_m2"] = round(ring_area_m2(ring))
else:
    print("no OSM outline cache (run fetch_osm.py): substations get no footprint")

countries = pd.DataFrame({"code": sorted(set(buses.country))})
countries["name"] = countries.code.map(iso_name)

# --- edges -------------------------------------------------------------------
country_of = buses.set_index("id").country

lines = read_pypsa("lines").rename(columns={
    "line_id": "id", "bus0": "from", "bus1": "to", "s_nom": "capacity_mva", "i_nom": "i_nom_ka",
    "tags": "osm_ids"})
lines["length_km"] = (lines.pop("length") / 1000).round(2)
lines["underground"] = flag(lines.underground)
lines["cross_border"] = lines["from"].map(country_of) != lines["to"].map(country_of)
lines = lines[["id", "from", "to", "voltage", "circuits", "capacity_mva", "i_nom_ka", "r", "x",
               "b", "length_km", "underground", "cross_border", "type", "osm_ids"]]

hvdc = read_pypsa("links").rename(columns={
    "link_id": "id", "bus0": "from", "bus1": "to", "p_nom": "capacity_mw", "tags": "osm_ids"})
hvdc["length_km"] = (hvdc.pop("length") / 1000).round(2)
hvdc["underground"] = flag(hvdc.underground)
hvdc["cross_border"] = hvdc["from"].map(country_of) != hvdc["to"].map(country_of)
hvdc = hvdc[["id", "from", "to", "voltage", "capacity_mw", "length_km", "underground",
             "cross_border", "osm_ids"]]

trafos = read_pypsa("transformers").rename(columns={
    "transformer_id": "id", "bus0": "from", "bus1": "to", "s_nom": "capacity_mva",
    "voltage_bus0": "voltage_from", "voltage_bus1": "voltage_to"})
# A transformer is placed at its primary (higher-voltage) bus
trafos["lon"] = trafos["from"].map(buses.set_index("id").lon)
trafos["lat"] = trafos["from"].map(buses.set_index("id").lat)

# --- provisional substation merge groups (inspect, do not merge) -------------------
# Some transformers join buses of two substations a few dozen metres apart: one site mapped
# as two OSM objects. Substations linked by such transformers share a merge group.
sub_of = buses.set_index("id").substation_id
t_subs = pd.DataFrame({"a": trafos["from"].map(sub_of), "b": trafos["to"].map(sub_of)})
trafos["spans_substations"] = t_subs.a != t_subs.b
parent = {}


def root(x):
    parent.setdefault(x, x)
    while parent[x] != x:
        parent[x] = parent[parent[x]]
        x = parent[x]
    return x


for a, b in t_subs[trafos.spans_substations].itertuples(index=False):
    parent[root(a)] = root(b)
groups = pd.Series({x: root(x) for x in parent})
group_ids = {r: f"merge-{i:03d}" for i, r in enumerate(sorted(set(groups)), 1)}
subs["merge_group"] = subs.id.map(groups.map(group_ids))
trafos["merge_group"] = t_subs.a.map(subs.set_index("id").merge_group).where(
    trafos.spans_substations)

convs = read_pypsa("converters").rename(columns={
    "converter_id": "id", "bus0": "from", "bus1": "to", "p_nom": "capacity_mw"})

# --- power plants ------------------------------------------------------------
pp = pd.read_csv(RAW / "transmission" / "powerplants.csv")
pp = pp[(pp.Capacity >= MIN_PLANT_MW) & (pp.DateOut.isna() | (pp.DateOut >= CURRENT_YEAR))]
# Attach each plant to the nearest AC bus in its own country (nearest overall put ~500 plants
# on a bus across a border). Plants in a country without transmission buses are dropped.
ac = buses[~buses.dc]
pp = pp.assign(country_code=pp.Country.map(name_iso), bus_id=None, bus_km=np.inf)
for cc, idx in pp.groupby("country_code").groups.items():
    cand = ac[ac.country == cc]
    if cand.empty:
        continue
    b_idx, b_km = nearest(pp.loc[idx, "lat"].values, pp.loc[idx, "lon"].values,
                          cand.lat.values, cand.lon.values)
    pp.loc[idx, "bus_id"] = cand.id.values[b_idx]
    pp.loc[idx, "bus_km"] = b_km.round(2)
keep = pp.bus_km <= np.where(pp.Technology == "Offshore", MAX_OFFSHORE_KM, MAX_PLANT_KM)
dropped, pp = pp[~keep], pp[keep]
plants = pd.DataFrame({
    "id": "ppm-" + pp.id.astype(str), "name": pp.Name, "fuel": pp.Fueltype,
    "technology": pp.Technology, "set": pp.Set, "country": pp.country_code,
    "capacity_mw": pp.Capacity, "year_in": pp.DateIn.astype("Int64"),
    "lon": pp.lon, "lat": pp.lat, "bus_id": pp.bus_id, "bus_km": pp.bus_km})

# --- CIM-style topology: every terminal -> ConnectionNode -> Bus ------------------
topo = Topology(dict(zip(buses.id, zip(buses.lon, buses.lat))))
for df in (lines, hvdc):
    for i, a, b in zip(df.id, df["from"], df["to"]):
        topo.to_bus(i, "Line", 1, a)
        topo.to_bus(i, "Line", 2, b)
for i, a, b, va, vb in zip(trafos.id, trafos["from"], trafos["to"], trafos.voltage_from,
                           trafos.voltage_to):
    topo.to_bus(topo.winding(i, "primary", va), "TransformerWinding", 1, a)  # bus0: higher voltage
    topo.to_bus(topo.winding(i, "secondary", vb), "TransformerWinding", 1, b)
for i, a, b in zip(convs.id, convs["from"], convs["to"]):                # bus0 is the DC side
    topo.to_bus(i, "Converter", 1, a)
    topo.to_bus(i, "Converter", 2, b)
for i, b in zip(plants.id, plants.bus_id):
    topo.to_bus(i, "PowerPlant", 1, b)

# --- write -------------------------------------------------------------------
for name, df in [("countries", countries), ("substations", subs), ("buses", buses),
                 ("lines", lines), ("hvdc", hvdc), ("transformers", trafos),
                 ("converters", convs), ("plants", plants)]:
    df.to_csv(OUT / f"{name}.csv", index=False)
    print(f"{name:16s} {len(df):>6}")
for name, n in topo.write(OUT).items():
    print(f"{name:16s} {n:>6}")
print(f"plants dropped (too far from grid): {len(dropped)}, "
      f"{dropped.Capacity.sum():.0f} MW; unmapped plant countries: "
      f"{sorted(set(pp.Country[plants.country.isna().values]))}")
