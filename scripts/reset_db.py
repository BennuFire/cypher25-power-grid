"""Delete ALL data, constraints and indexes in the configured database (for a clean rebuild).

Label names (tokens) survive this, so the label order set up in cim.LABEL_ORDER only
applies on a brand-new database (e.g. a freshly created Aura instance).
"""
from db import Graph


def main():
    with Graph() as g:
        # CALL ... IN TRANSACTIONS needs an implicit (auto-commit) transaction
        with g.driver.session(database=g.database) as session:
            session.run("MATCH (n) CALL (n) { DETACH DELETE n } IN TRANSACTIONS OF 5000 ROWS"
                        ).consume()
        for kind, query in (("CONSTRAINT", "SHOW CONSTRAINTS YIELD name"),
                            ("INDEX", "SHOW INDEXES YIELD name, type WHERE type <> 'LOOKUP'")):
            recs, _, _ = g.run(query)
            for r in recs:
                g.run(f"DROP {kind} `{r['name']}` IF EXISTS")
        print("database empty:", g.counts())


if __name__ == "__main__":
    main()
