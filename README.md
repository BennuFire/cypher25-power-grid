# Electric Grid graph — Neo4j Cypher 25 path modes & QPP

Material for a blog post and a NeoDash / Aura Dashboards demo that shows Neo4j's
quantified path patterns (QPP) and the GQL path modes `WALK`, `TRAIL` and `ACYCLIC`
(Neo4j 2026.03+, Cypher 25) on two power grids in one database:

| Layer | Source | Scope | Source label |
|---|---|---|---|
| Transmission | PyPSA-Eur OSM network + powerplantmatching | Europe (ENTSO-E area, 35 countries), 220–750 kV, plants ≥ 10 MW | `:PyPSA` |
| Distribution | SimBench `1-MVLV-urban-all-0-sw` | One synthetic urban German grid, 110 / 10 / 0.4 kV | `:SimBench` |

The two layers are tied by **one synthetic transformer** (`:Transformer:Synthetic`) between the
SimBench supply bus and the nearest real 380 kV PyPSA bus (5 km away, near Schwerin). It is
not real infrastructure: it exists so that queries can cross both layers. There is exactly one
because SimBench gives every grid a single supply point (its `ext_grid`, an equivalent of the whole
upstream network), and our PyPSA data has no 110 kV lines that could feed the SimBench substation.

## Graph model (CIM-style)

Electrical connectivity uses **only `[:CONNECTS]`**. Every piece of equipment is a node, and each
of its terminals points to a `ConnectionNode`, which points to a `Bus`:

```
(:Line)-[:CONNECTS {terminal: 1}]->(:ConnectionNode)-[:CONNECTS]->(:Bus)
(:Line)-[:CONNECTS {terminal: 2}]->(:ConnectionNode)-[:CONNECTS]->(:Bus)

(:Transformer)-[:HAS_WINDING]->(:TransformerWinding {side: 'primary'})-[:CONNECTS]->(:ConnectionNode)-[:CONNECTS]->(:Bus)
(:Transformer)-[:HAS_WINDING]->(:TransformerWinding {side: 'secondary'})-[:CONNECTS]->(:ConnectionNode)-[:CONNECTS]->(:Bus)
```

A `ConnectionNode` is a **junction**: when a switch sits in front of a line end, the switch's
second terminal and the line's terminal share one ConnectionNode, and that node has no bus:

```
(:Bus)<-(:ConnectionNode)<-[t1]-(:Switch {closed})-[t2]->(:ConnectionNode)<-(:Line)
```

| Node label | Layer | Notes |
|---|---|---|
| `Country`, `Substation` | PyPSA | `(:Substation)-[:IN_COUNTRY]->(:Country)`, `(:Substation)-[:HAS_BUS]->(:Bus)`, `(:Substation)-[:HAS_TRANSFORMER]->(:Transformer)`. Names: the real OSM name of the substation (`nameSource: 'osm'`, 3,869 of 6,218, e.g. "Poste électrique d'Échalas"; merged sites join names with ` / `). Otherwise generated from the nearest GeoNames town in the same country, with a ≥ 200k city within 20 km winning and city districts excluded (`nameSource: 'city'`). Duplicates get ` #2`, ` #3`, …; `osmName` and `nearCity` / `nearCityKm` keep the raw values. Footprint: `footprint` is the yard outline from OSM as a closed list of points (4,214 substations): the OSM outline as is (`footprintKind: 'outline'`), or the convex hull when the site is several OSM objects or a relation in pieces (`'convexHull'`). `areaM2` is its area; `location` stays PyPSA's point, which falls inside the outline for 94% of sites. `virtual_*` sites have no footprint |
| `Bus` | both | SimBench buses also carry a voltage-level label from their own nominal voltage: `:HV` (≥ 60 kV, here 110 kV), `:MV` (≥ 1 kV, here 10 kV), `:LV` (0.4 kV) |
| `ConnectionNode` | both | Placed at the coordinates of the bus it belongs to. Most connect one equipment terminal to a bus; SimBench switch **junctions** join a switch's second terminal and the line or winding behind it, and have **no** bus edge (20,924 of them) |
| `Line` | both | `:Line:HVDC` for the 39 HVDC links. SimBench lines (all underground cables) carry their real cable type (`stdType`) and per-km values `rOhmPerKm`, `xOhmPerKm`, `cNfPerKm`; PyPSA lines store modelled totals `r`, `x`, `b` |
| `Transformer`, `TransformerWinding` | both | windings have `side` (`primary` = supply / higher-voltage side, `secondary`), `voltage`, and a voltage-level label `:HV` / `:MV` / `:LV` (same thresholds as buses) |
| `Converter` | PyPSA | AC/DC converter stations (terminal 1 = DC side, 2 = AC side) |
| `Switch` | SimBench | `closed`, `type` (CB, LBS, LS, DS); 15 are open (normally open points) |
| `PowerPlant` | PyPSA | from powerplantmatching (a separate dataset, see Sources), connected through a ConnectionNode to the nearest AC bus **in its own country**. This is an approximation: the source doesn't say where a plant connects, and many plants connect below 220 kV, which isn't in the graph |
| `Consumer`, `Generator` | SimBench | 11,542 loads with `segment` from their German BDEW load profile (`H0` household 10,225; `G0`–`G6` commercial 1,315; 2 large 10 kV loads with profiles `BL-H` / `WB-H` as `other`). `pMW` is SimBench's reference power that the yearly `profile` scales, not a measurement. 806 generators: 805 rooftop PV at 0.4 kV (10.7 MW) and 1 hydro unit at 10 kV (2.9 MW) |

**Label order.** The source label comes first and the asset type last: `['PyPSA', 'Bus']`,
`['SimBench', 'MV', 'Bus']`, `['PyPSA', 'HVDC', 'Line']`. Neo4j orders `labels(n)` by when each
label name first appeared in the database (Neo4j Browser colors a node by the first one), so
`cim.SCHEMA` creates the source and qualifier labels before any asset label. Label names survive
`reset_db.py`, so this only works on a **brand-new** database.

**Modelled electrical values (PyPSA).** Treat these as illustrative, good for filtering and
weighting paths, not as real equipment data:

- *Lines:* `type` is one of 4 PyPSA standard overhead conductors, one per voltage class (e.g.
  `Al/St 240/40 4-bundle 380.0`), and `r`, `x`, `b`, `iNomKA` follow from it. Every 380 kV line has the
  same per-km values (r ≈ 0.03 Ω/km, x ≈ 0.25 Ω/km, b ≈ 4 µS/km per circuit). The stored values are
  totals for the line with its circuits in parallel. `capacityMVA` is exactly √3 × voltage ×
  `iNomKA` × `circuits`. The 510 underground cables also get overhead-conductor values, which is
  physically wrong for cables.
- *Transformers:* a `Transformer` stands for the **total transformation between two voltage levels
  at a site**, not one physical unit (no two share a bus pair). `capacityMVA` (median 4,425 MVA, up
  to 23,238) is not a nameplate rating. The common values are multiples of a line rating, e.g.
  1,788 MVA = one 400 kV line, 3,575 = two. So it is sized to the connected lines, as a modelling
  assumption.

**Known data quirk.** 258 PyPSA transformers connect buses of two different substations that
are 22–121 m apart. This is the same site mapped as two OSM objects, often with a `virtual_…`
substation that PyPSA generates. They are **not merged**. These transformers have `HAS_TRANSFORMER` from both
substations, and these provisional properties flag them for inspection:

- `Transformer.spansSubstations`: `true` for the 258 transformers.
- `Substation.mergeGroup` / `Transformer.mergeGroup`: `merge-001` … `merge-241`, shared by
  substations that are linked through such transformers (239 pairs, 2 groups of three).

```cypher
MATCH (s:Substation) WHERE s.mergeGroup IS NOT NULL
WITH s.mergeGroup AS grp, collect(s) AS subs
MATCH (t:Transformer {mergeGroup: grp})
RETURN grp, [x IN subs | x.name] AS substations, collect(t.id) AS transformers,
       max(point.distance(subs[0].location, subs[-1].location)) AS metresApart
```

Size on AuraDB Free (limit 200k nodes / 400k relationships): **178,424 nodes, 189,685
relationships**.

## What we take from OpenStreetMap

- **Provenance.** PyPSA buses, substations, lines and HVDC links carry `osmIds`, the OpenStreetMap
  objects they were built from (PyPSA's `tags` column, e.g. `['way/311562553', 'way/311569354']`).
  Look any of them up at `https://www.openstreetmap.org/<id>`. A substation's `osmIds` combines those
  of its buses.
- **Live OSM data, fetched separately.** `fetch_osm.py` asks the Overpass API for the current tags of
  all 8,542 objects and the outlines of the 4,367 `power=substation` ones, and caches them in
  `data/raw/osm/` (fetched 3–5 Oct 2026; 36 objects had been deleted from OSM since PyPSA's
  extraction). The data is therefore newer than the PyPSA release it is matched to.
- **What the objects are.** 4,367 are substations, but 3,693 are `power=line`, plus 156 circuits and
  130 cables. PyPSA's `virtual_*` substations (1,976) are built from the **lines** that end there,
  where nobody mapped a substation. So they have no OSM name and no outline.
- **Names.** 91% of the real substations have an OSM `name`: 3,869 of 6,218 `Substation` nodes
  (`nameSource: 'osm'`). The rest are named after a nearby town (`nameSource: 'city'`).
- **Footprints.** 4,214 substations get `footprint` + `areaM2` from their OSM outline (median yard
  ≈ 25,000 m²). PyPSA's own point lies inside the outline for 94% of them.
- **Attribution.** Everything here is © OpenStreetMap contributors, ODbL.

## Future options

### Line route geometry (PyPSA)
`prepare_transmission.py` drops the WKT `geometry` column on read. The raw files still have it:
all 9,162 lines and the 39 HVDC links are `LINESTRING`s, with 792,557 points in total (median 49 per
line, at most 2,258). Bus geometry repeats `x`/`y`, and transformer/converter geometry is 2-point
stubs, so those add nothing. The geometry also confirms that `length` is in **metres**: it matches
the length measured along each line to within ±1.5%.

Neo4j's only native spatial type is `POINT`, so the storage options are:

| Option | How | Notes |
|---|---|---|
| **A. List of points** (preferred) | `Line.route = [point(...), ...]`, simplified first (50–100 m tolerance) | Native and queryable in Cypher (bounding box, near a city); no extra nodes |
| B. WKT text | `Line.wkt = 'LINESTRING (...)'` | Simplest; Cypher can't query it spatially |
| C. Vertex nodes | one node per point | ~793k nodes, over the Aura Free limit |

Still open, for line routes and for the substation `footprint` polygons alike: whether NeoDash or
Aura Dashboards map reports can draw a polyline or polygon from a list-of-points property. Today they
draw relationships as straight lines between located nodes.

## Layout

```
scripts/
  db.py                    Neo4j connection helper (reads .env)
  cim.py                   CIM topology builder + shared schema + topology loader
  geo.py                   shared geometry: haversine distances, nearest point, convex hull, area
  fetch_osm.py             OSM tags + substation outlines via Overpass -> data/raw/osm (cached)
  prepare_transmission.py  raw PyPSA-Eur / powerplantmatching / GeoNames -> data/import/transmission
  prepare_distribution.py  SimBench (via the simbench package) -> data/import/distribution
  reset_db.py              deletes ALL data, constraints and indexes in the database
  load_transmission.py     loads data/import/transmission into Neo4j
  load_distribution.py     loads data/import/distribution into Neo4j (run after transmission)
  bench_restoration.py     Q1/Q2 restoration: ACYCLIC / allReduce / old syntax -> blog/results/restoration_<q>.csv
  bench_line_trip.py       Q3 N-1 routes: ACYCLIC vs post-filter vs TRAIL at 8/10/12 hops -> blog/results/line_trip.csv
queries/                   showcase questions as .cypher files (q1 transformer failure, q1b any source,
                           q2 10 kV cable fault, q3 N-1 line trip routes + bottlenecks); runnable in
                           Neo4j Browser with the :param line
data/raw/                  downloaded sources (git-ignored); data/raw/osm/ = Overpass cache + fetch.log
data/import/               cleaned CSVs, ready to load
blog/                      post.md (article draft) and results/ (benchmark CSVs and logs)
dashboard/                 NeoDash / Aura Dashboards exports
```

## Rebuild from scratch

```bash
py -m venv .venv
.venv/Scripts/python -m pip install pandas numpy neo4j python-dotenv simbench
cp .env.example .env       # fill in from the Aura credentials file

# raw data (see Sources for what each file is)
mkdir -p data/raw/transmission data/raw/geonames
for f in buses lines links transformers converters; do
  curl -L "https://zenodo.org/api/records/18619025/files/$f.csv/content" -o data/raw/transmission/$f.csv
done
curl -L https://raw.githubusercontent.com/PyPSA/powerplantmatching/master/powerplants.csv -o data/raw/transmission/powerplants.csv
curl -L https://download.geonames.org/export/dump/cities1000.zip -o data/raw/geonames/cities1000.zip
curl -L https://download.geonames.org/export/dump/countryInfo.txt -o data/raw/geonames/countryInfo.txt
unzip data/raw/geonames/cities1000.zip -d data/raw/geonames

cd scripts
../.venv/Scripts/python prepare_transmission.py
../.venv/Scripts/python fetch_osm.py             # slow (public Overpass); resumable
../.venv/Scripts/python prepare_transmission.py   # again, now with real OSM names
../.venv/Scripts/python prepare_distribution.py   # SimBench data ships inside the pip package
../.venv/Scripts/python reset_db.py               # careful: wipes the target database
../.venv/Scripts/python load_transmission.py
../.venv/Scripts/python load_distribution.py
```

**Reloading.** The loaders MERGE everything on `id`, so rerunning them updates properties and adds
what's missing without creating duplicates. But MERGE **never deletes**: if connectivity or IDs
changed (e.g. a plant moved to another bus), the old nodes and edges stay. In that case run
`reset_db.py` first. Every load step also counts the rows that make it through its query and stops
with an error if any were dropped, e.g. an edge pointing to a bus that doesn't exist.

## License

Code: **[MIT](https://opensource.org/license/mit)** (full text in `LICENSE`). Blog text: **CC BY 4.0**. Derived data (`data/import/`):
**ODbL 1.0**, because it is built from OpenStreetMap, PyPSA-Eur and SimBench (all ODbL); power plants
and GeoNames place names keep their CC BY 4.0 attribution. Blog post, results and dashboards must
carry an attribution notice. Details and the notice text: [LICENSE.md](LICENSE.md).
© 2026 Harold Agudelo.

## Sources

### Data
- **PyPSA-Eur prebuilt electricity network based on OpenStreetMap**, v0.7 (12 Feb 2026).
  Xiong, Fioriti, Neumann, Riepin, Brown. Zenodo:
  <https://zenodo.org/records/12799201> (concept DOI 10.5281/zenodo.12799201; version used:
  10.5281/zenodo.18619025). License: **ODbL 1.0**.
  - Paper: Xiong et al., *Modelling the high-voltage grid using open data for Europe and
    beyond*, Scientific Data 12, 277 (2025): <https://www.nature.com/articles/s41597-025-04550-7>
    (preprint: <https://arxiv.org/abs/2408.17178>)
  - PyPSA-Eur model: <https://github.com/PyPSA/pypsa-eur>, base-network docs:
    <https://pypsa-eur.readthedocs.io/en/v2025.07.0/data-base-network.html>
  - Substation names, `power` tags and outlines are fetched from OpenStreetMap through the Overpass API
    (`scripts/fetch_osm.py`, cached in `data/raw/osm/`). © OpenStreetMap contributors, ODbL.
  - Interactive map of the network by the dataset author: <https://bxio.ng/assets/osm>
    (the Zenodo record also ships it as `map.html`)
  - Built from OpenStreetMap data, so attribution is required: **© OpenStreetMap contributors**,
    <https://www.openstreetmap.org/copyright>
- **powerplantmatching** (PyPSA project): <https://github.com/PyPSA/powerplantmatching>,
  file <https://raw.githubusercontent.com/PyPSA/powerplantmatching/master/powerplants.csv>.
  Docs: <https://powerplantmatching.readthedocs.io/>. Data license: **CC BY 4.0**. Filtered to units ≥ 10 MW that are still operating, within
  100 km of a transmission bus in the same country (250 km for offshore wind, e.g. German North Sea
  farms that export to Germany even though the Dutch coast is closer).
- **GeoNames**: `cities1000` (places ≥ 1,000 people) and `countryInfo` from <https://download.geonames.org/export/dump/>,
  used to name substations and countries (field description:
  <https://download.geonames.org/export/dump/readme.txt>). License: **CC BY 4.0**, <https://www.geonames.org/>.
- **SimBench**: benchmark dataset for LV/MV/HV/EHV grid analysis by Uni Kassel, Fraunhofer
  IEE, RWTH Aachen and TU Dortmund. Site: <https://simbench.de/>, Python package:
  <https://pypi.org/project/simbench/>, code and docs: <https://github.com/e2nIEE/simbench>,
  <https://simbench.readthedocs.io/>, downloads and data documentation: <https://simbench.de/en/download/>,
  dataset on Zenodo: <https://zenodo.org/record/2844372>.
  License: **ODbL 1.0** (contents: DbCL 1.0). Paper: Meinecke et al., *SimBench — A Benchmark Dataset of Electric
  Power Systems to Compare Innovative Solutions Based on Power Flow Analysis*, Energies 13(12),
  3290 (2020), <https://doi.org/10.3390/en13123290>.
- Background on ENTSO-E data that was **not** used as a source: the ENTSO-E grid map is
  a published map, not a reusable dataset. The ENTSO-E Transparency Platform
  (<https://transparency.entsoe.eu/>) has measurements such as flows and load, not topology.

### Neo4j
- ACYCLIC paths: <https://neo4j.com/docs/cypher-manual/current/patterns/acyclic-paths/>
- Match modes and path modes: <https://neo4j.com/docs/cypher-manual/25/patterns/reference/match-modes-and-path-modes/>
- Cypher patterns overview: <https://neo4j.com/docs/cypher-manual/current/patterns/>
- Neo4j blog, *Declarative route planning with Cypher 25*: <https://neo4j.com/blog/developer/declarative-route-planning-with-cypher-25/>
- Aura Dashboards import from NeoDash: <https://neo4j.com/docs/aura/dashboards/import/>
- NeoDash archival discussion: <https://community.neo4j.com/t/aura-dashboards-missing-many-features-as-compared-to-neodash-2-4/75091>
