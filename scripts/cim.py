"""CIM-style connectivity shared by both layers (PyPSA transmission, SimBench distribution).

Electrical connectivity uses only [:CONNECTS]:
    (equipment)-[:CONNECTS {terminal: n}]->(:ConnectionNode)-[:CONNECTS]->(:Bus)
Equipment = Line, Switch, TransformerWinding, Converter, PowerPlant, Consumer, Generator.
A ConnectionNode is a junction: where a switch meets a line end, both terminals share one
ConnectionNode that has no bus. Windings belong to their transformer through
(:Transformer)-[:HAS_WINDING]->(:TransformerWinding).
"""
import pandas as pd

LABELS = {"Bus", "ConnectionNode", "Line", "Switch", "TransformerWinding", "Converter",
          "PowerPlant", "Consumer", "Generator"}

# labels(n) is ordered by label-token id, i.e. by when a label name first appeared in the
# database (Browser colors nodes by the first one). Creating the source and qualifier labels
# before any asset label gives ['PyPSA', 'Bus'], ['SimBench', 'MV', 'Bus'], ...
# Tokens are never deleted, so this only takes effect on a fresh database.
LABEL_ORDER = ["PyPSA", "SimBench", "Synthetic", "HVDC", "HV", "MV", "LV"]


def level(kv):
    """Voltage-level label for buses and windings: HV >= 60 kV, MV >= 1 kV, else LV."""
    return "HV" if kv >= 60 else "MV" if kv >= 1 else "LV"


# Adds the :HV / :MV / :LV label in row.level to node `n`
SET_LEVEL = """
        CALL (n, row) {
          WITH n WHERE row.level = 'HV' SET n:HV
          UNION WITH n WHERE row.level = 'MV' SET n:MV
          UNION WITH n WHERE row.level = 'LV' SET n:LV
        }"""

SCHEMA = [
    *(f"CREATE (n:{label}) DELETE n" for label in LABEL_ORDER),
    "CREATE CONSTRAINT country_code IF NOT EXISTS FOR (n:Country) REQUIRE n.code IS UNIQUE",
    *(f"CREATE CONSTRAINT {label.lower()}_id IF NOT EXISTS FOR (n:{label}) REQUIRE n.id IS UNIQUE"
      for label in sorted(LABELS | {"Substation", "Transformer"})),
    "CREATE INDEX substation_name IF NOT EXISTS FOR (n:Substation) ON (n.name)",
    "CREATE INDEX bus_name IF NOT EXISTS FOR (n:Bus) ON (n.name)",
    "CREATE INDEX bus_voltage IF NOT EXISTS FOR (n:Bus) ON (n.voltage)",
    "CREATE INDEX line_voltage IF NOT EXISTS FOR (n:Line) ON (n.voltage)",
    "CREATE INDEX line_name IF NOT EXISTS FOR (n:Line) ON (n.name)",
    "CREATE INDEX switch_closed IF NOT EXISTS FOR (n:Switch) ON (n.closed)",
    "CREATE INDEX plant_fuel IF NOT EXISTS FOR (n:PowerPlant) ON (n.fuel)",
    "CREATE POINT INDEX substation_location IF NOT EXISTS FOR (n:Substation) ON (n.location)",
]


class Topology:
    """Collects ConnectionNodes, CONNECTS edges and windings while preparing a layer."""

    def __init__(self, bus_xy):
        self.bus_xy = bus_xy          # bus id -> (lon, lat), used to place ConnectionNodes
        self.nodes = {}               # ConnectionNode id -> (lon, lat)
        self.edges = []               # (from, from_label, to, to_label, terminal)
        self.windings = []

    def junction(self, cn_id, placed_at):
        """Create a ConnectionNode at the coordinates of bus `placed_at`, WITHOUT connecting
        it to that bus (to_bus adds the bus edge; a switch junction has none)."""
        self.nodes[cn_id] = self.bus_xy[placed_at]
        return cn_id

    def terminal(self, eq_id, eq_label, terminal, cn_id):
        self.edges.append((eq_id, eq_label, cn_id, "ConnectionNode", terminal))

    def to_bus(self, eq_id, eq_label, terminal, bus_id):
        """Equipment terminal -> its own ConnectionNode -> bus."""
        cn = self.junction(f"cn:{eq_id}:{terminal}", bus_id)
        self.terminal(eq_id, eq_label, terminal, cn)
        self.edges.append((cn, "ConnectionNode", bus_id, "Bus", None))
        return cn

    def winding(self, transformer_id, side, voltage):
        """Add a winding ('primary' = supply / higher-voltage side, 'secondary') to a transformer
        and return its id; attach it with to_bus/terminal. Bus voltage levels (HV/MV/LV labels)
        are a separate concept, so windings deliberately avoid HV/LV naming."""
        wid = f"{transformer_id}:{side}"
        self.windings.append((wid, transformer_id, side, voltage, level(float(voltage))))
        return wid

    def write(self, out_dir):
        pd.DataFrame([(k, *v) for k, v in self.nodes.items()],
                     columns=["id", "lon", "lat"]).to_csv(out_dir / "connection_nodes.csv",
                                                          index=False)
        edges = pd.DataFrame(self.edges, columns=["from", "from_label", "to", "to_label",
                                                  "terminal"])
        edges["terminal"] = edges.terminal.astype("Int64")  # 1, 2 or empty, not 1.0
        assert set(edges.from_label) | set(edges.to_label) <= LABELS
        edges.to_csv(out_dir / "connects.csv", index=False)
        pd.DataFrame(self.windings, columns=["id", "transformer_id", "side", "voltage", "level"]
                     ).to_csv(out_dir / "windings.csv", index=False)
        return {"connection_nodes": len(self.nodes), "connects": len(edges),
                "windings": len(self.windings)}


def load_topology(g, data_dir, layer):
    """Load connection_nodes / windings / connects CSVs written by Topology.write().

    Run after the equipment nodes exist. `layer` is the source label (PyPSA or SimBench).
    """
    def read(name):
        df = pd.read_csv(data_dir / f"{name}.csv", dtype=str, keep_default_na=False)
        return df.replace("", float("nan"))

    from db import records  # local import: prepare scripts use this module without a driver

    g.load(f"""
        MERGE (n:ConnectionNode {{id: row.id}})
        SET n:{layer}, n.location = point({{longitude: toFloat(row.lon), latitude: toFloat(row.lat)}})
        """, records(read("connection_nodes")))
    g.load(f"""
        MERGE (n:TransformerWinding {{id: row.id}})
        SET n:{layer}, n.side = row.side, n.voltage = toFloat(row.voltage), n.level = row.level
        WITH n, row
        MATCH (t:Transformer {{id: row.transformer_id}})
        MERGE (t)-[:HAS_WINDING]->(n)
        SET n.location = t.location
        WITH n, row
        {SET_LEVEL}
        """, records(read("windings")))
    edges = read("connects")
    for (fl, tl), part in edges.groupby(["from_label", "to_label"]):
        assert {fl, tl} <= LABELS, (fl, tl)  # labels are interpolated, so whitelist them
        g.load(f"""
            MATCH (a:{fl} {{id: row.from}}), (b:{tl} {{id: row.to}})
            MERGE (a)-[r:CONNECTS]->(b)
            SET r.terminal = toInteger(row.terminal)""", records(part))
    return len(edges)
