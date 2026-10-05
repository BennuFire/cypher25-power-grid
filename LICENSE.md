# Licenses

This repository contains three kinds of material, licensed separately:

| What | Where | License |
|---|---|---|
| Code | `scripts/`, `queries/` | [MIT](https://opensource.org/license/mit) (full text: `LICENSE`) |
| Blog text | `blog/post.md` | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) |
| Data derived from open databases | `data/import/` | [ODbL 1.0](https://opendatacommons.org/licenses/odbl/1-0/) (see below) |

The `LICENSE` file (MIT) applies to the code only.

## Code

Copyright (c) 2026 Harold Agudelo. Released under the [MIT License](https://opensource.org/license/mit) (full text in the `LICENSE` file): use, copy, modify
and distribute it freely, keeping the copyright notice; no warranty.

## Blog text

Copyright (c) 2026 Harold Agudelo. The article in `blog/post.md` is licensed under
[Creative Commons Attribution 4.0 International (CC BY 4.0)](https://creativecommons.org/licenses/by/4.0/):
you may share and adapt it, including commercially, as long as you credit
"Harold Agudelo" and link to the original. Data shown in the article keeps its own attribution
requirements (see *Produced works* below); code snippets in it are also available under MIT.

## Data

### Sources

| Source | License | Required attribution | Used for |
|---|---|---|---|
| [PyPSA-Eur prebuilt network based on OpenStreetMap](https://zenodo.org/records/12799201) v0.7 (Xiong et al.) | [ODbL 1.0](https://opendatacommons.org/licenses/odbl/1-0/) | "© OpenStreetMap contributors"; cite the dataset | transmission buses, lines, HVDC, transformers, converters |
| [OpenStreetMap](https://www.openstreetmap.org/copyright), via the Overpass API (fetched 3–5 Oct 2026) | ODbL 1.0 | "© OpenStreetMap contributors" | substation names, `power` tags, footprints |
| [SimBench](https://simbench.de/) (`1-MVLV-urban-all-0-sw`, Uni Kassel, Fraunhofer IEE, RWTH Aachen, TU Dortmund) | ODbL 1.0, contents [DbCL 1.0](https://opendatacommons.org/licenses/dbcl/1-0/) | cite SimBench | distribution grid |
| [powerplantmatching](https://github.com/PyPSA/powerplantmatching) `powerplants.csv` | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) | cite powerplantmatching | power plants |
| [GeoNames](https://www.geonames.org/) `cities1000`, `countryInfo` | CC BY 4.0 | "GeoNames (geonames.org)" | generated substation names, country names |

The raw downloads in `data/raw/` are **not** redistributed (they are git-ignored); the README
explains how to fetch them from their publishers.

### The derived database: ODbL 1.0

The cleaned CSVs in `data/import/` (and any Neo4j database built from them) are a **derivative
database** of the ODbL sources above. They are made available under the
**[Open Database License (ODbL) 1.0](https://opendatacommons.org/licenses/odbl/1-0/)**; the
individual contents are under the [Database Contents License (DbCL) 1.0](https://opendatacommons.org/licenses/dbcl/1-0/).
Parts that come from CC BY 4.0 sources (power plants, GeoNames place names) keep their
attribution requirement.

If you use or adapt this data publicly, you must:

- **attribute** it: "Contains information from OpenStreetMap (© OpenStreetMap contributors),
  the PyPSA-Eur network and SimBench, made available under the ODbL; power plants from
  powerplantmatching and place names from GeoNames (CC BY 4.0)";
- **share alike**: offer any derived database you publish under the ODbL as well;
- **keep it open**: don't distribute it only with technical restrictions (e.g. DRM).

What this project **adds** to the sources (substation grouping and names, footprints as convex
hulls, plant-to-bus links, merge flags, the CIM connectivity model, and the one
`:Synthetic` transformer that ties the two grids) is part of the derived database and is
licensed the same way.

### Produced works

The blog post, benchmark tables (`blog/results/`), charts and dashboards are **produced works**
under the ODbL: they are not themselves under the ODbL, but must carry an attribution notice such
as the one above.

*This note summarises the license terms for convenience; it is not legal advice. The license
texts linked above are authoritative.*
