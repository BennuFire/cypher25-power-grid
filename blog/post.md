# Loops, rings and N-1: power-grid questions with Cypher 25 path modes

For almost three years I worked at a large energy company as a solutions engineer, migrating
grid data into a CIM-like model on relational databases. Exploration questions such as *"which
customers lose power if this cable fails?"* were a struggle there, so we moved them to Neo4j.
That was much better, but with the Cypher of those days some answers were still cumbersome to
write. Cypher 25's quantified path patterns and path modes change that game, and this post shows
how, on an open European grid.

Electric grids are graphs with a twist: they are full of loops on purpose. Transmission grids
are meshed so that power can flow around a failed line, and distribution grids are built as
rings that are *operated* as trees by keeping a few switches open. That makes them a great test
for quantified path patterns and two recent additions to Cypher (recent in database years:
`allReduce` turned one in August):

- **Quantified path patterns (QPP)**: repeat a whole sub-pattern, with conditions inside each
  repetition, e.g. `((a)-[r]->(b) WHERE r.closed)+`.
- **`allReduce`** (Neo4j 2025.08, Cypher 25): keeps a running value along a path, such as open
  switches crossed or km travelled, and drops the path the moment a condition on it fails.
  Before, a condition inside a QPP could only look at the current repetition.
- **Path modes** (Neo4j 2026.03, Cypher 25): say how a path may repeat itself.
  - `WALK`: anything goes, nodes and relationships may repeat.
  - `TRAIL`: no relationship twice, but nodes may repeat.
  - `ACYCLIC`: no node twice: a real route, no loops.

`ACYCLIC` is the one that wasn't possible before. Until 2026.03, Cypher matched paths with
"no relationship twice", which is what `TRAIL` means today, and offered **no way to forbid
revisiting a node while matching**. To get loop-free routes you had to build every path and
throw away the ones with a repeated node afterwards. As we'll see, that difference decides
whether some grid questions finish at all.

Rather than benchmarking abstract path counts, this post asks the questions a grid operator
asks, answers them in Cypher, and then measures what the new features actually change. Spoiler:
the answer is "it depends on whether your graph has loops", and the grid shows both cases.

> **TL;DR**
> - Pruning *while* the path grows (`allReduce`, conditions inside a QPP) answers a restoration
>   question with **175× fewer db hits** than checking afterwards.
> - In the meshed transmission grid, `TRAIL` finds **154× too many** "alternative routes", and
>   even `SHORTEST 10` returns routes that loop, unless you add `ACYCLIC`.
> - `ACYCLIC` is not a free lunch: at 12 hops it still runs out of memory, until you add a
>   budget (a 400 km limit finishes in **2 s**).

All queries below need **Cypher 25**: start them with `CYPHER 25` unless your database already
defaults to it, otherwise `ACYCLIC` and `allReduce` are syntax errors.

## The data: two grids in one database

| Layer | Source | What | Label |
|---|---|---|---|
| Transmission | [PyPSA-Eur OSM network](https://zenodo.org/records/12799201) (v0.7) + OpenStreetMap | European 220–750 kV grid: 6,863 buses, 9,162 lines, 39 HVDC links, 878 transformers, plus 12,210 power plants from [powerplantmatching](https://github.com/PyPSA/powerplantmatching) | `:PyPSA` |
| Distribution | [SimBench](https://simbench.de/) `1-MVLV-urban-all-0-sw` | A synthetic but realistic German urban grid: 110 / 10 / 0.4 kV, 10,328 cables, 20,933 switches, 11,542 customers | `:SimBench` |

The two layers are joined by **one synthetic 380/110 kV transformer** (labelled `:Synthetic`)
from the SimBench supply point to the nearest real 380 kV bus near Schwerin. It isn't real
infrastructure; it's there so that, for this one synthetic German district, a single graph runs
from the European transmission grid down to individual households. Everywhere else, the graph
stops at the 220 kV+ substations. Substation names and footprints come from OpenStreetMap (3,869 real names such as
"Poste électrique de Villejust"). Everything fits in a free AuraDB instance: 178,424 nodes and
189,685 relationships.

### A CIM-style model

The graph follows the idea of the power-industry standard CIM: **equipment is a node**, and
electrical connectivity uses a single relationship type, `CONNECTS`. Each terminal of a line,
switch, winding or customer points to a `ConnectionNode`, which points to a `Bus`:

```
(:Bus)<-[:CONNECTS]-(:ConnectionNode)<-[:CONNECTS {terminal: 1}]-(:Line)-[:CONNECTS {terminal: 2}]->(:ConnectionNode)-[:CONNECTS]->(:Bus)
(:Transformer)-[:HAS_WINDING]->(:TransformerWinding {side: 'primary' | 'secondary'})-[:CONNECTS]->...
```

A switch sits *between* a bus and the line it switches, so an open switch really cuts the path:

```
(:Bus)<-(:ConnectionNode)<-(:Switch {closed: false})->(:ConnectionNode)<-(:Line)
```

This is where QPP earns its keep. Crossing **one line** from bus to bus is now four
relationships, and it has to leave the line through the *other* terminal. QPP lets us repeat
exactly that unit, with a condition that compares two relationships *in the same repetition*:

```cypher
((:Bus)<-[:CONNECTS]-(:ConnectionNode)<-[t1:CONNECTS]-(:Line)-[t2:CONNECTS]->(:ConnectionNode)-[:CONNECTS]->(:Bus)
  WHERE t1.terminal <> t2.terminal)+
```

Without that `WHERE`, a `WALK` could enter a line and come straight back out: 86 million
"paths" of 8 hops from a single substation instead of 392,118.

## Warm-up: how many customers does a transformer feed?

> *"Which customers are supplied by transformer X, crossing closed switches only?"*

```cypher
CYPHER 25
MATCH (:Transformer {id: $t})-[:HAS_WINDING]->(w:TransformerWinding {side: 'secondary'})
MATCH p = ACYCLIC (w) (()-[:CONNECTS|HAS_WINDING]-(n 
    WHERE (NOT n:Switch OR n.closed)
    AND coalesce(n.voltage, 0) <= w.voltage)
)+ (c:Consumer)
RETURN count(DISTINCT c) AS customers
```

The condition inside the pattern does two jobs: only cross closed switches, and never climb to
a higher voltage (so the search goes *down* through transformers, never up into the 110 kV
side and the European grid behind it).

For the main 110/10 kV transformer ("Trafo1") the answer is **4,215 customers**, and every
variant we tried took about 0.3 s: with `ACYCLIC`, without it, and even with the old
`-[:CONNECTS|HAS_WINDING*]-` syntax plus `WHERE all(n IN nodes(p) …)`.

That is the first lesson. **With closed switches only, a distribution grid is a tree**: 4,215
customers, 4,215 paths, exactly one each. A tree has no loops for `ACYCLIC` to remove, and
Neo4j's planner already applies an `all(n IN nodes(p) WHERE …)` filter *during* the traversal:
the plan shows a `VarLengthExpand(Pruning)` operator with the condition inside it.
Path modes are not magic; they matter when the graph you're allowed to walk has loops.

*(These timings are single runs: within that noise the variants are equal, not ranked.)*

## Q1 — A transformer fails: who can take over?

> *"Trafo1 (110/10 kV) fails. Which of its customers can Trafo2 take over by closing at most
> **one** switch that is open today, and which switch?"*

Now the loops are back. To answer, the search must be allowed to cross open switches, and open
switches are exactly what break the rings. The trick is to allow **at most one**, and to stop a
path the moment it would need a second. That's what `allReduce` does: it keeps a running value
while the path grows, and drops the path as soon as the condition fails:

```cypher
CYPHER 25
// 1. the supply units Trafo1 feeds today (closed switches, at its 10 kV level)
MATCH (:Transformer {id: $failed})-[:HAS_WINDING]->(w1:TransformerWinding {side: 'secondary'})
MATCH ACYCLIC (w1) (()-[:CONNECTS|HAS_WINDING]-(n WHERE (NOT n:Switch OR n.closed)
                     AND coalesce(n.voltage, w1.voltage) = w1.voltage))+ (u)
WHERE (u:TransformerWinding AND u.side = 'primary') OR u:Consumer
WITH collect(DISTINCT u) AS lost
// 2. reach them from Trafo2, never through Trafo1, crossing at most ONE open switch
MATCH (:Transformer {id: $backup})-[:HAS_WINDING]->(w:TransformerWinding {side: 'secondary'})
MATCH p = ACYCLIC (w) (()-[:CONNECTS|HAS_WINDING]-(n WHERE coalesce(n.voltage, w.voltage) = w.voltage
                     AND NOT (n.id = $failed OR n.id STARTS WITH $failed + ':')))+ (u)
  WHERE u IN lost
    AND allReduce(open = 0, x IN n | open + CASE WHEN x:Switch AND NOT x.closed THEN 1 ELSE 0 END,
                  open <= 1)
WITH u, [x IN n WHERE x:Switch AND NOT x.closed] AS opened
WHERE size(opened) = 1
RETURN opened[0].name AS closeSwitch, count(DISTINCT u) AS unitsRestored
```

The query works like a control room: it restores *supply units* at 10 kV (secondary
substations, plus customers connected directly at 10 kV) and then counts the customers behind
them.
([full query](https://github.com/BennuFire/cypher25-power-grid/blob/main/queries/q1_restoration.cypher))

**Answer:** all **4,215 customers** (18.6 MW reference load) can be re-supplied, through any
one of **9 switches**:

| Switch to close | Type | Customers |
|---|---|---|
| `CB MV3.101 Busbar4` / `Busbar5` | circuit breaker (busbar coupler) | 4,215 |
| `CB MV3.101 Base Station2` | disconnector | 4,215 |
| 6 × `loop_line_switch` | load-break switch | 4,215 |

The busbar couplers are the realistic move: they join the dead busbar to the live one, and
Trafo2 (63 MVA) then carries 18.6 + 31.1 ≈ 50 MW. The loop-line switches are topologically
valid but would push everything through a single 10 kV cable rated 6–9 MVA. **The query checks
connectivity, not ratings.**

A variant without a named backup ([q1b](https://github.com/BennuFire/cypher25-power-grid/blob/main/queries/q1b_restoration_any_source.cypher)) treats
every transformer feeding the same voltage level as a candidate and finds Trafo2 on its own.
Run in reverse, Trafo1 can take over Trafo2's 7,327 customers the same way; 4,215 + 7,327 =
11,542, every customer in the grid.

### What the features are worth

Same question, same answer, five ways of searching:

| Variant | Median time | db hits |
|---|---|---|
| **V1** `ACYCLIC` + QPP + `allReduce` | **0.21 s** | **390 k** |
| V2 QPP + `allReduce` (no `ACYCLIC`) | 0.18 s | 398 k |
| V3 `ACYCLIC` + QPP, open-switch count checked afterwards | 9.6 s | 68.2 M (175×) |
| V4 QPP only | did not finish in 120 s | — |
| V5 old variable-length syntax, all checks afterwards | did not finish in 120 s | — |

- **`allReduce` brings the speed**: 175× fewer db hits than counting afterwards, because the
  search never wanders into the rings.
- **`ACYCLIC` makes it finish at all**: without either feature, the query doesn't end.
- **With pruning, `ACYCLIC` costs nothing**: V1 and V2 are within measurement noise, which
  makes it a free safety net.

## Q2 — A 10 kV cable fails

> *"A 10 kV cable fails and is isolated. Which customers lose supply, and which single open
> switch brings them back?"*

Leaving the faulted cable out of the traversal is the same as isolating it. The query compares
what all sources supply today with what they supply without the cable, and then searches for
one-switch restorations exactly like Q1 ([full query](https://github.com/BennuFire/cypher25-power-grid/blob/main/queries/q2_cable_fault.cypher)).

For `MV3.101 Line 3`: **769 customers** lose supply, and only **2 of the 15 open switches**
help: `loop_line_switch 1.2` or `7.2`, the two ends of that feeder's ring. Unlike Q1, the
answer really depends on *where* the fault is.

Because each answer takes under a second, we can ask it for **every** 10 kV cable (147 of
them, 30 s in total):

- 12 faults cut nobody off: the 11 normally-open loop lines and the reserve line.
- 135 faults cut customers off: median 540, worst **1,622** (Line 67).
- **All 135 can be fully restored by closing a single switch**: 1 option for 80 faults, 2 for
  37, 3 for 14, 4 for 4. That is what ring-designed 10 kV grids are built for, and the sweep
  confirms it for every cable of this grid in about 30 seconds.

Benchmark on the worst case, Line 67 (1,622 customers, 2 switch options):

| Variant | Median time | db hits |
|---|---|---|
| **V1** `ACYCLIC` + QPP + `allReduce` | **0.13 s** | **275 k** |
| V2 QPP + `allReduce` (no `ACYCLIC`) | 0.13 s | 279 k |
| V3 `ACYCLIC` + QPP, checked afterwards | 5.95 s | 37.8 M (137×) |
| V4 QPP only | did not finish in 120 s | — |
| V5 old variable-length syntax | did not finish in 120 s | — |

Same pattern as Q1, on a different question.

## Q3 — N-1 in the meshed transmission grid

*N-1* is the basic security rule of power grids: a grid with *N* elements (lines, transformers,
generators) must keep running when any single one of them fails, without cutting off customers
or overloading what's left. European transmission operators plan and run their grids to it. A
full N-1 study needs a power-flow calculation to check those loads; the graph answers the
topological half of the question: which routes are left?

> *"The 400 kV line Villejust – Cirolliers (south of Paris, 19.8 km) trips. Which loop-free
> routes still connect its two ends within 10 hops, how much longer are they, and which lines
> do they all depend on?"*

Here there is no tree to fall back on. The European transmission grid is meshed everywhere, and
the hop is the four-relationship line crossing from earlier:

```cypher
CYPHER 25
MATCH (x:Line {id: $tripped})-[:CONNECTS {terminal: 1}]->(:ConnectionNode)-[:CONNECTS]->(a:Bus),
      (x)-[:CONNECTS {terminal: 2}]->(:ConnectionNode)-[:CONNECTS]->(b:Bus)
MATCH p = ACYCLIC (a) ((:Bus)<-[:CONNECTS]-(:ConnectionNode)<-[t1:CONNECTS]-(l:Line WHERE l <> x)
                       -[t2:CONNECTS]->(:ConnectionNode)-[:CONNECTS]->(:Bus)
                       WHERE t1.terminal <> t2.terminal){1,10} (b)
```

([routes](https://github.com/BennuFire/cypher25-power-grid/blob/main/queries/q3_line_trip_routes.cypher),
[bottlenecks](https://github.com/BennuFire/cypher25-power-grid/blob/main/queries/q3_line_trip_bottlenecks.cypher))

**Answer:** the query finds **834 routes**. Each one is a sequence of *lines*, so two routes count
as different as soon as they use a different line anywhere. That matters, because many stretches
of this grid are covered by several parallel lines. Take the route through Nozay, hop by hop:

| Hop | Parallel lines to choose from |
|---|---|
| Villejust → Nozay | 4 |
| Nozay → Dambron | 1 |
| Dambron → Gâtinais | 2 |
| Gâtinais → Cirolliers | 2 |

That is 4 × 1 × 2 × 2 = **16 routes** through the same five substations. An operator would see
them as one **corridor**. Grouping the 834 routes by their sequence of substations (the buses
along the path) leaves **28 corridors**, and the query keeps the shortest route of each:

```cypher
WITH x, [n IN nodes(p) WHERE n:Bus] AS buses, reduce(km = 0.0, r IN l | km + r.lengthKm) AS km
WITH x, buses, count(*) AS circuitVariants, min(km) AS km   // one row per corridor
```

| Hops | Circuit combinations | km | Detour | Via |
|---|---|---|---|---|
| 1 | 2 | 20.0 | 1.0× | the two parallel lines of the tripped one |
| 4 | 16 | 167.3 | 8.4× | Nozay → Dambron → Gâtinais |
| 6 | 16 | 291.1 | 14.7× | … → Boismorand → Chesnoy |

And the bottleneck: apart from the parallel circuits, **every** alternative crosses
**Dambron – Nozay** (832 of 834 routes). Lose that line too, and the 400 kV level has no detour
left. (The search crosses lines only, so it stays at 400 kV. The 225 kV grid underneath,
reached through transformers, would add detours.)

### Here `ACYCLIC` is the whole point

| Max hops | Variant | Routes (corridors) | Median time | db hits |
|---|---|---|---|---|
| 8 | **`ACYCLIC`** | 226 (8) | **0.31 s** | **1.4 M** |
| 8 | post-filter: the only option before 2026.03 | 226 (8) | 1.23 s | 7.3 M (5×) |
| 8 | `TRAIL` | 9,626 (205) ✗ | 1.26 s | 9.1 M |
| 10 | **`ACYCLIC`** | 834 (28) | **2.06 s** | **13.7 M** |
| 10 | post-filter: the only option before 2026.03 | 834 (28) | 19.5 s | 123.8 M (9×) |
| 10 | `TRAIL` | 128,378 (1,264) ✗ | 21.9 s | 154.0 M |
| 12 | all three | — | out of memory | — |

`TRAIL` isn't just slower: it gives a **different answer** (✗). It forbids reusing a
relationship but not revisiting a substation, so "out on one parallel circuit and straight back
on the other" counts as a route: 154 times too many at 10 hops. Of its 128,378 routes, 127,544
revisit a substation, and the remaining 834 are exactly the `ACYCLIC` answer. The post-filter,
the only way to get loop-free routes before 2026.03, gets the right answer the slow way: it
builds every path first and throws most of them away. `ACYCLIC` never builds them, and its
advantage grows with every hop (5× at 8, 9× at 10).

But `ACYCLIC` is not a free lunch either. At **12 hops all three variants run out of Aura
Free's 2.2 GiB transaction memory**, `ACYCLIC` included, after 15 seconds. The number of
*genuinely* loop-free routes in a mesh still grows exponentially with the hop limit. A longer
horizon therefore needs a budget. We tried two, both at 12 hops:

```cypher
// a) no route longer than 400 km: allReduce keeps the running length and stops the path early
MATCH p = ACYCLIC (a) (...){1,12} (b) WHERE allReduce(km = 0.0, r IN l | km + r.lengthKm, km <= 400)
// b) only the 10 shortest routes (by hops)
MATCH p = SHORTEST 10 ACYCLIC (a) (...){1,12} (b)
```

| 12 hops | Result | Median time |
|---|---|---|
| `ACYCLIC` + 400 km budget | 1,410 routes in 14 corridors | **2.0 s** |
| post-filter + 400 km budget | out of memory | — |
| `SHORTEST 10 ACYCLIC` | 10 loop-free routes | **0.05 s** |
| `SHORTEST 10`, no path mode | 10 routes, **8 of them revisit a substation** | 0.04 s |

The budget only works *together with* `ACYCLIC`: within 400 km there are still enough loops to
exhaust memory. And note the last row: even a "shortest paths" query returns loops without a
path mode. After the two direct parallel lines, the next-shortest routes bounce back and forth
over parallel circuits.

## Takeaways

1. **Prune inside the pattern.** Conditions in a QPP (`(n WHERE …)`) and `allReduce` run while
   the path grows. That is where the 100×+ differences come from.
2. **Use `ACYCLIC` when the graph you walk has loops.** In a tree it is free; in a mesh or a
   ring it is what makes queries finish, and what makes the *answer* correct. It doesn't remove
   the exponential growth of real routes, though: add a budget (a km limit with `allReduce`, or
   `SHORTEST k`) for long searches. That turned an out-of-memory query into a 2 s one.
3. **`TRAIL` is not "routes", and neither is `SHORTEST` on its own.** If you mean alternative
   routes, you mean `ACYCLIC`.
4. **Model the domain, then let QPP repeat it.** A CIM-style graph makes one "hop" four
   relationships with a rule attached; QPP expresses that directly.

## Caveats

- All answers are **topology only**: no power flow, no cable or transformer ratings.
- PyPSA line and transformer parameters are **modelled** (standard types), not equipment data;
  SimBench is **synthetic** by design, and the tie between the two layers is invented.
- Timings come from a **shared AuraDB Free** instance: a warm-up plus the median of 3 runs.
  Times are indicative; db hits are the stable metric.

## Data and credits

- Grid: PyPSA-Eur prebuilt network based on OpenStreetMap, Xiong et al., *Scientific Data* 12,
  277 (2025), ODbL. Substation names and outlines: **© OpenStreetMap contributors**, ODbL.
- Power plants: powerplantmatching, CC BY 4.0. Place names: GeoNames, CC BY 4.0.
- Distribution grid: SimBench, Meinecke et al., *Energies* 13(12), 3290 (2020), ODbL.
- Neo4j docs: [ACYCLIC paths](https://neo4j.com/docs/cypher-manual/current/patterns/acyclic-paths/),
  [match modes and path modes](https://neo4j.com/docs/cypher-manual/25/patterns/reference/match-modes-and-path-modes/);
  `allReduce`: [Neo4j 2025.08 release notes](https://neo4j.com/release-notes/database/neo4j-2025-08/),
  [Solve hard graph problems with Cypher 25](https://neo4j.com/blog/developer/solve-hard-graph-problems-with-cypher-25/).

Everything, from the download scripts to the benchmarks, is in the
[project repository](https://github.com/BennuFire/cypher25-power-grid) (`scripts/`, `queries/`, `blog/results/`).
The derived grid database is published under the ODbL 1.0 ([license note](https://github.com/BennuFire/cypher25-power-grid/blob/main/LICENSE.md)).

---

*© 2026 Harold Agudelo. This article is licensed under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/); the code is under the [MIT License](https://opensource.org/license/mit).*
