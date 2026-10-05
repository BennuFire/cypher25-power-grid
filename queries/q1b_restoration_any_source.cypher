// Q1b — Restoration after a transformer failure, without naming a backup
//
// "Trafo1 (110/10 kV) fails. Which OTHER transformers can take over its customers by closing
//  at most ONE switch that is open today — and through which switch?"
//
// Candidate sources: every transformer whose secondary winding feeds the same voltage level as
// the failed one (here 10 kV), i.e. the other 110/10 kV transformers. 10/0.4 kV transformers
// feed 0.4 kV and are not candidates. Same method as q1_restoration.cypher: works at the 10 kV
// level on supply units, then counts the customers behind them. Topology only (no ratings).
//
// Neo4j Browser:  :param {failed: 'sb-trafo-133'}
CYPHER 25

// 1. Supply units the failed transformer feeds today (closed switches, its 10 kV level)
MATCH (:Transformer {id: $failed})-[:HAS_WINDING]->(w1:TransformerWinding {side: 'secondary'})
MATCH ACYCLIC (w1) (()-[:CONNECTS|HAS_WINDING]-(n WHERE (NOT n:Switch OR n.closed)
                     AND coalesce(n.voltage, w1.voltage) = w1.voltage))+ (u)
WHERE (u:TransformerWinding AND u.side = 'primary') OR u:Consumer
WITH w1, collect(DISTINCT u) AS lost

// 2. Candidate sources: other transformers feeding the same voltage level
MATCH (src:Transformer)-[:HAS_WINDING]->(w:TransformerWinding {side: 'secondary'})
WHERE src.id <> $failed AND w.voltage = w1.voltage

// 3. Reach the lost units from each candidate, never through the failed transformer,
//    crossing at most one open switch (allReduce prunes while the path grows)
MATCH p = ACYCLIC (w) (()-[:CONNECTS|HAS_WINDING]-(n WHERE coalesce(n.voltage, w.voltage) = w.voltage
                     AND NOT (n.id = $failed OR n.id STARTS WITH $failed + ':')))+ (u)
  WHERE u IN lost
    AND allReduce(open = 0, x IN n | open + CASE WHEN x:Switch AND NOT x.closed THEN 1 ELSE 0 END,
                  open <= 1)
WITH src, u, [x IN n WHERE x:Switch AND NOT x.closed] AS opened
WHERE size(opened) = 1                      // needs exactly one switch closed
WITH src, opened[0] AS closeSwitch, collect(DISTINCT u) AS units

// 4. Customers behind the restored units
CALL (units) {
  UNWIND units AS u
  OPTIONAL MATCH (u)<-[:HAS_WINDING]-(:Transformer)-[:HAS_WINDING]->(s:TransformerWinding {side: 'secondary'})
  OPTIONAL MATCH ACYCLIC (s) (()-[:CONNECTS|HAS_WINDING]-(n WHERE (NOT n:Switch OR n.closed)
                               AND coalesce(n.voltage, 0) <= s.voltage))+ (c:Consumer)
  WITH CASE WHEN u:Consumer THEN u ELSE c END AS customer
  WITH DISTINCT customer WHERE customer IS NOT NULL
  RETURN count(customer) AS customers, round(sum(customer.pMW), 2) AS referenceMW
}
RETURN src.name AS source, src.voltageFrom + '/' + src.voltageTo + ' kV' AS sourceVoltage,
       src.capacityMVA AS sourceMVA, closeSwitch.name AS closeSwitch, closeSwitch.type AS type,
       size(units) AS units, customers, referenceMW
ORDER BY source, customers DESC, closeSwitch
