"""Shared Neo4j connection helpers. Reads .env (the Aura credentials file format)."""
import math
import os
from pathlib import Path

from dotenv import load_dotenv
from neo4j import GraphDatabase

ROOT = Path(__file__).resolve().parents[1]


class Graph:
    def __init__(self):
        load_dotenv(ROOT / ".env")
        self.database = os.environ.get("NEO4J_DATABASE") or None
        self.driver = GraphDatabase.driver(
            os.environ["NEO4J_URI"],
            auth=(os.environ["NEO4J_USERNAME"], os.environ["NEO4J_PASSWORD"]))
        self.driver.verify_connectivity()

    def run(self, cypher, **params):
        return self.driver.execute_query(cypher, params, database_=self.database)

    def load(self, cypher, rows, batch=2000):
        """UNWIND rows (list of dicts) through cypher, which sees each one as `row`.

        Fails if any row doesn't make it through the query: a MATCH that finds no node (e.g. an
        edge to a missing bus) silently drops its row, so we count the rows that survive.
        Checked after each batch commits, so on failure fix the data and rerun (MERGE is safe).
        """
        for i in range(0, len(rows), batch):
            part = rows[i:i + batch]
            recs, _, _ = self.run(f"UNWIND $rows AS row {cypher} RETURN count(*) AS done",
                                  rows=part)
            if recs[0]["done"] != len(part):
                raise RuntimeError(f"{len(part) - recs[0]['done']} of {len(part)} rows dropped "
                                   f"(missing node?) in:\n{cypher.strip()}")

    def counts(self):
        recs, _, _ = self.run(
            "RETURN COUNT{()} as nodes,  COUNT{()-->()} as rels")
        return recs[0].data()

    def close(self):
        self.driver.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def records(df):
    """DataFrame -> list of dicts with NaN turned into null."""
    return [{k: (None if isinstance(v, float) and math.isnan(v) else v) for k, v in r.items()}
            for r in df.to_dict("records")]
