// Q3 — N-1 in the transmission grid: line X trips, which corridors are left?
//
// "The 400 kV line Villejust – Cirolliers trips. Which loop-free routes still connect its two
//  ends within 10 line hops — and how much longer are the shortest ones?"
//
// One hop = crossing one line from bus to bus. In the CIM model that is 4 relationships:
//   (Bus)<-(ConnectionNode)<-(Line)->(ConnectionNode)->(Bus)
// The WHERE inside the hop makes it leave the line through the OTHER terminal (no U-turn
// inside a line). ACYCLIC: a route never visits the same bus twice — so "out on one parallel
// circuit and straight back on the other" is not counted as an alternative.
//
// Routes are grouped into CORRIDORS (the sequence of substations): parallel circuits on the
// same towers multiply routes (16-64 per corridor here) that an operator sees as one corridor.
// Hops cross lines only, so routes stay at the tripped line's voltage level (no transformers,
// so the 225 kV grid underneath is not used).
// The hop limit (10) is a literal: Cypher does not accept a parameter in {1,10}.
//
// Neo4j Browser:  :param {tripped: 'relation/6554523-400'}
CYPHER 25

MATCH (x:Line {id: $tripped})-[:CONNECTS {terminal: 1}]->(:ConnectionNode)-[:CONNECTS]->(a:Bus),
      (x)-[:CONNECTS {terminal: 2}]->(:ConnectionNode)-[:CONNECTS]->(b:Bus)
MATCH p = ACYCLIC (a) ((:Bus)<-[:CONNECTS]-(:ConnectionNode)<-[t1:CONNECTS]-(l:Line WHERE l <> x)
                       -[t2:CONNECTS]->(:ConnectionNode)-[:CONNECTS]->(:Bus)
                       WHERE t1.terminal <> t2.terminal){1,10} (b)
WITH x, [n IN nodes(p) WHERE n:Bus] AS buses, reduce(km = 0.0, r IN l | km + r.lengthKm) AS km

// one row per corridor: how many circuit combinations, and the shortest of them
WITH x, buses, count(*) AS circuitVariants, min(km) AS km
WITH x, count(*) AS corridors, sum(circuitVariants) AS routes,
     collect({buses: buses, variants: circuitVariants, km: km}) AS all
UNWIND all AS c
WITH x, corridors, routes, c ORDER BY c.km
RETURN corridors, routes, size(c.buses) - 1 AS hops, c.variants AS circuitVariants,
       round(c.km, 1) AS km, round(c.km / x.lengthKm, 2) AS detour,
       [bus IN c.buses | head([(s:Substation)-[:HAS_BUS]->(bus) | s.name])] AS via
LIMIT 10
