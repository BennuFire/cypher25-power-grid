// Q1 — Restoration after a transformer failure (SimBench distribution grid)
//
// "Trafo1 (110/10 kV) fails. Which of its customers can Trafo2 take over by closing at most
//  ONE switch that is open today — and which switch?"
//
// Works at the 10 kV level, like a control room: it restores supply units (10/0.4 kV
// transformers + customers connected at 10 kV), then counts the customers behind them.
// Topology only: it does not check cable or transformer ratings.
//
// Neo4j Browser:  :param {failed: 'sb-trafo-133', backup: 'sb-trafo-134'}
CYPHER 25

// 1. Supply units Trafo1 feeds today: closed switches only, staying at its 10 kV level
//    (connection nodes and transformers have no voltage, so coalesce lets them through)
MATCH (:Transformer {id: $failed})-[:HAS_WINDING]->(w1:TransformerWinding {side: 'secondary'})
MATCH ACYCLIC (w1) (()-[:CONNECTS|HAS_WINDING]-(n WHERE (NOT n:Switch OR n.closed)
                     AND coalesce(n.voltage, w1.voltage) = w1.voltage))+ (u)
WHERE (u:TransformerWinding AND u.side = 'primary') OR u:Consumer
WITH collect(DISTINCT u) AS lost

// 2. Reach those units from Trafo2, never through Trafo1, crossing AT MOST ONE open switch.
//    allReduce keeps a running count of open switches while the path grows and drops the
//    path the moment it would need a second one — that pruning is what makes this fast.
MATCH (:Transformer {id: $backup})-[:HAS_WINDING]->(w:TransformerWinding {side: 'secondary'})
MATCH p = ACYCLIC (w) (()-[:CONNECTS|HAS_WINDING]-(n WHERE coalesce(n.voltage, w.voltage) = w.voltage
                     AND NOT (n.id = $failed OR n.id STARTS WITH $failed + ':')))+ (u)
  WHERE u IN lost
    AND allReduce(open = 0, x IN n | open + CASE WHEN x:Switch AND NOT x.closed THEN 1 ELSE 0 END,
                  open <= 1)
WITH u, [x IN n WHERE x:Switch AND NOT x.closed] AS opened
WHERE size(opened) = 1                      // needs exactly one switch closed
WITH opened[0] AS closeSwitch, collect(DISTINCT u) AS units

// 3. Customers behind the restored units: each 10/0.4 kV transformer's low-voltage tree
CALL (units) {
  UNWIND units AS u
  OPTIONAL MATCH (u)<-[:HAS_WINDING]-(:Transformer)-[:HAS_WINDING]->(s:TransformerWinding {side: 'secondary'})
  OPTIONAL MATCH ACYCLIC (s) (()-[:CONNECTS|HAS_WINDING]-(n WHERE (NOT n:Switch OR n.closed)
                               AND coalesce(n.voltage, 0) <= s.voltage))+ (c:Consumer)
  WITH CASE WHEN u:Consumer THEN u ELSE c END AS customer
  WITH DISTINCT customer WHERE customer IS NOT NULL
  RETURN count(customer) AS customers, round(sum(customer.pMW), 2) AS referenceMW
}
RETURN closeSwitch.name AS closeSwitch, closeSwitch.type AS type, size(units) AS units,
       customers, referenceMW
ORDER BY customers DESC, closeSwitch
