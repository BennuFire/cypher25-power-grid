// Q2 — 10 kV cable fault: who loses supply, and which switch brings them back?
//
// "A 10 kV cable fails and is isolated. Which supply units (10/0.4 kV transformers + customers
//  connected at 10 kV) lose supply — and which single open switch, if closed, re-supplies them
//  from any transformer feeding that voltage level?"
//
// Leaving the faulted cable out of the traversal is the same as isolating it (opening the
// switches at both of its ends). Topology only: it does not check cable or transformer ratings.
//
// Neo4j Browser:  :param {faulted: 'MV3.101 Line 3'}
CYPHER 25

MATCH (f:Line:SimBench {name: $faulted})

// 1. Sources: transformers whose secondary feeds the faulted cable's voltage level
MATCH (:Transformer)-[:HAS_WINDING]->(w:TransformerWinding {side: 'secondary'})
WHERE w.voltage = f.voltage
WITH f, collect(w) AS sources

// 2. Units supplied today vs. with the cable isolated (closed switches, that voltage level)
CALL (f, sources) {
  UNWIND sources AS w
  MATCH ACYCLIC (w) (()-[:CONNECTS|HAS_WINDING]-(n WHERE (NOT n:Switch OR n.closed)
                       AND coalesce(n.voltage, w.voltage) = w.voltage))+ (u)
  WHERE (u:TransformerWinding AND u.side = 'primary') OR u:Consumer
  RETURN collect(DISTINCT u) AS before
}
CALL (f, sources) {
  UNWIND sources AS w
  MATCH ACYCLIC (w) (()-[:CONNECTS|HAS_WINDING]-(n WHERE (NOT n:Switch OR n.closed)
                       AND coalesce(n.voltage, w.voltage) = w.voltage AND n <> f))+ (u)
  WHERE (u:TransformerWinding AND u.side = 'primary') OR u:Consumer
  RETURN collect(DISTINCT u) AS after
}
WITH f, sources, [u IN before WHERE NOT u IN after] AS lost

// 3. Re-supply the lost units from any source around the fault, crossing at most one open
//    switch (allReduce drops a path the moment it would need a second one)
UNWIND sources AS w
OPTIONAL MATCH p = ACYCLIC (w) (()-[:CONNECTS|HAS_WINDING]-(n WHERE coalesce(n.voltage, w.voltage) = w.voltage
                                  AND n <> f))+ (u)
  WHERE u IN lost
    AND allReduce(open = 0, x IN n | open + CASE WHEN x:Switch AND NOT x.closed THEN 1 ELSE 0 END,
                  open <= 1)
WITH lost, u, [x IN n WHERE x:Switch AND NOT x.closed] AS opened
WITH lost, opened[0] AS closeSwitch, collect(DISTINCT u) AS units   // closeSwitch null: nothing restorable

// 4. Customers behind units: the restored ones per switch, and all lost ones for context
CALL (units) {
  UNWIND units AS u
  OPTIONAL MATCH (u)<-[:HAS_WINDING]-(:Transformer)-[:HAS_WINDING]->(s:TransformerWinding {side: 'secondary'})
  OPTIONAL MATCH ACYCLIC (s) (()-[:CONNECTS|HAS_WINDING]-(n WHERE (NOT n:Switch OR n.closed)
                               AND coalesce(n.voltage, 0) <= s.voltage))+ (c:Consumer)
  WITH DISTINCT CASE WHEN u:Consumer THEN u ELSE c END AS customer
  RETURN count(customer) AS customers
}
CALL (lost) {
  UNWIND lost AS u
  OPTIONAL MATCH (u)<-[:HAS_WINDING]-(:Transformer)-[:HAS_WINDING]->(s:TransformerWinding {side: 'secondary'})
  OPTIONAL MATCH ACYCLIC (s) (()-[:CONNECTS|HAS_WINDING]-(n WHERE (NOT n:Switch OR n.closed)
                               AND coalesce(n.voltage, 0) <= s.voltage))+ (c:Consumer)
  WITH DISTINCT CASE WHEN u:Consumer THEN u ELSE c END AS customer
  RETURN count(customer) AS customersLost
}
RETURN size(lost) AS unitsLost, customersLost,
       closeSwitch.name AS closeSwitch, closeSwitch.type AS type,
       size(units) AS unitsRestored, customers AS customersRestored
ORDER BY customersRestored DESC, closeSwitch
