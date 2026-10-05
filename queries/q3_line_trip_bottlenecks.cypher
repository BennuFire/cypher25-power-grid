// Q3b — N-1 bottlenecks: which lines carry most of the remaining routes?
//
// "After line X trips, which other lines appear in the most loop-free alternative routes
//  between its two ends (within 10 hops)? If one of THOSE trips next, the most alternatives
//  disappear." Same route search as q3_line_trip_routes.cypher.
//
// Neo4j Browser:  :param {tripped: 'relation/6554523-400'}
CYPHER 25

MATCH (x:Line {id: $tripped})-[:CONNECTS {terminal: 1}]->(:ConnectionNode)-[:CONNECTS]->(a:Bus),
      (x)-[:CONNECTS {terminal: 2}]->(:ConnectionNode)-[:CONNECTS]->(b:Bus)
MATCH p = ACYCLIC (a) ((:Bus)<-[:CONNECTS]-(:ConnectionNode)<-[t1:CONNECTS]-(l:Line WHERE l <> x)
                       -[t2:CONNECTS]->(:ConnectionNode)-[:CONNECTS]->(:Bus)
                       WHERE t1.terminal <> t2.terminal){1,10} (b)
WITH count(p) AS routes, collect(l) AS lineLists
UNWIND lineLists AS lines
UNWIND lines AS line
WITH routes, line, count(*) AS inRoutes
MATCH (line)-[:CONNECTS {terminal: 1}]->(:ConnectionNode)-[:CONNECTS]->(:Bus)<-[:HAS_BUS]-(s1:Substation),
      (line)-[:CONNECTS {terminal: 2}]->(:ConnectionNode)-[:CONNECTS]->(:Bus)<-[:HAS_BUS]-(s2:Substation)
RETURN line.id AS line, s1.name + ' – ' + s2.name AS between, line.voltage AS kV,
       round(line.lengthKm, 1) AS km, inRoutes, routes,
       round(100.0 * inRoutes / routes, 1) AS pctOfRoutes
ORDER BY inRoutes DESC
LIMIT 10
