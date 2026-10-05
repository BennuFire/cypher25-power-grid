"""Turn the SimBench urban MV/LV grid (1-MVLV-urban-all-0-sw, ODbL) into Neo4j import CSVs.

SimBench is a synthetic-but-realistic German benchmark grid (Uni Kassel / Fraunhofer IEE).
Uses the CIM-style model from cim.py. SimBench switches sit between a bus and the element
they switch, so the switch's second terminal and that element's terminal share a junction
ConnectionNode:
    (Bus)<-(CN)<-(Switch)->(CN)<-(Line)            line-end switch
    (Bus)<-(CN)<-(Switch)->(CN)<-(TransformerWinding)   transformer breaker
    (Bus)<-(CN)<-(Switch)->(CN)->(Bus)             bus coupler
The external-grid bus is tied to the nearest real transmission bus through a synthetic
(:Transformer:Synthetic) so queries can cross both layers.
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd
import simbench as sb

from cim import Topology, level
from geo import nearest

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "import" / "distribution"
OUT.mkdir(parents=True, exist_ok=True)
CODE = "1-MVLV-urban-all-0-sw"
SEGMENTS = {"H0": "household", "L0": "agricultural", "L1": "agricultural", "L2": "agricultural"}


net = sb.get_simbench_net(CODE)
bid = lambda i: f"sb-bus-{i}"      # noqa: E731
sid = lambda i: f"sb-switch-{i}"   # noqa: E731
tid = lambda i: f"sb-trafo-{i}"    # noqa: E731
lid = lambda i: f"sb-line-{i}"     # noqa: E731

# --- buses ---------------------------------------------------------------------
coords = net.bus.geo.map(lambda g: json.loads(g)["coordinates"])
buses = pd.DataFrame({
    "id": net.bus.index.map(bid), "name": net.bus.name, "voltage": net.bus.vn_kv,
    "level": net.bus.vn_kv.map(level), "kind": net.bus.type, "subnet": net.bus.subnet,
    "is_source": net.bus.index == net.ext_grid.bus.iloc[0],
    "lon": coords.str[0].values, "lat": coords.str[1].values})
lon_of = pd.Series(buses.lon.values, index=net.bus.index)   # pandapower bus index -> lon/lat
lat_of = pd.Series(buses.lat.values, index=net.bus.index)

# Synthetic tie target: nearest real (PyPSA) AC bus to the SimBench supply point
tx = pd.read_csv(ROOT / "data" / "import" / "transmission" / "buses.csv")
tx = tx[~tx.dc]
src = buses.loc[buses.is_source].iloc[0]
t_idx, t_km = nearest(np.array([src.lat]), np.array([src.lon]), tx.lat.values, tx.lon.values)
tie_bus, tie_km = tx.iloc[t_idx[0]], round(float(t_km[0]), 1)

topo = Topology(dict(zip(buses.id, zip(buses.lon, buses.lat)))
                | {tie_bus.id: (tie_bus.lon, tie_bus.lat)})

# --- switches: terminal 1 -> CN -> bus, terminal 2 -> junction CN ------------------
sw = net.switch
switches = pd.DataFrame({
    "id": sw.index.map(sid), "name": sw.name, "type": sw.type, "closed": sw.closed,
    "switches": sw.et.map({"l": "line", "t": "transformer", "b": "bus"}),
    "subnet": sw.subnet, "voltage": sw.bus.map(net.bus.vn_kv),
    "lon": sw.bus.map(lon_of), "lat": sw.bus.map(lat_of)})
junction = {}  # (element type, element, bus) -> junction CN the element attaches to
for i, et, el, b in zip(sw.index, sw.et, sw.element, sw.bus):
    topo.to_bus(sid(i), "Switch", 1, bid(b))
    if et == "b":
        topo.to_bus(sid(i), "Switch", 2, bid(el))
    else:
        cn = topo.junction(f"cn:{sid(i)}:2", bid(b))
        topo.terminal(sid(i), "Switch", 2, cn)
        junction[(et, el, b)] = cn


def attach(eq_id, label, terminal, et, element, bus):
    """Connect a terminal through its switch's junction CN if switched, else to the bus."""
    cn = junction.get((et, element, bus))
    if cn:
        topo.terminal(eq_id, label, terminal, cn)
    else:
        topo.to_bus(eq_id, label, terminal, bid(bus))


# --- lines ---------------------------------------------------------------------
ln = net.line
lines = pd.DataFrame({
    "id": ln.index.map(lid), "name": ln.name, "std_type": ln.std_type,
    "length_km": ln.length_km.round(4), "max_i_ka": ln.max_i_ka,
    # real values per cable type, per km (SimBench convention; PyPSA lines store totals)
    "r_ohm_per_km": ln.r_ohm_per_km, "x_ohm_per_km": ln.x_ohm_per_km,
    "c_nf_per_km": ln.c_nf_per_km,
    "kind": ln.type.map({"cs": "cable", "ol": "overhead"}), "subnet": ln.subnet,
    "voltage": ln.from_bus.map(net.bus.vn_kv)})
for i, f, t in zip(ln.index, ln.from_bus, ln.to_bus):
    attach(lid(i), "Line", 1, "l", i, f)
    attach(lid(i), "Line", 2, "l", i, t)

# --- transformers + windings -----------------------------------------------------
tr = net.trafo
trafos = pd.DataFrame({
    "id": tr.index.map(tid), "name": tr.name, "std_type": tr.std_type,
    "capacity_mva": tr.sn_mva, "voltage_from": tr.vn_hv_kv, "voltage_to": tr.vn_lv_kv,
    "subnet": tr.subnet, "lon": tr.hv_bus.map(lon_of), "lat": tr.hv_bus.map(lat_of)})
for i, hv, lv, vhv, vlv in zip(tr.index, tr.hv_bus, tr.lv_bus, tr.vn_hv_kv, tr.vn_lv_kv):
    attach(topo.winding(tid(i), "primary", vhv), "TransformerWinding", 1, "t", i, hv)
    attach(topo.winding(tid(i), "secondary", vlv), "TransformerWinding", 1, "t", i, lv)

# --- consumers & generators ----------------------------------------------------
ld = net.load
prefix = ld.profile.str.split("-").str[0]
consumers = pd.DataFrame({
    "id": ld.index.map(lambda i: f"sb-load-{i}"), "name": ld.name, "bus_id": ld.bus.map(bid),
    "p_mw": ld.p_mw, "profile": ld.profile,
    "segment": prefix.map(SEGMENTS).where(~prefix.str.startswith("G"), "commercial")
                     .fillna("other")})
sg = net.sgen
generators = pd.DataFrame({
    "id": sg.index.map(lambda i: f"sb-sgen-{i}"), "name": sg.name, "bus_id": sg.bus.map(bid),
    "type": sg.type, "p_mw": sg.p_mw, "profile": sg.profile})
for label, df in (("Consumer", consumers), ("Generator", generators)):
    for i, b in zip(df.id, df.bus_id):
        topo.to_bus(i, label, 1, b)
for df, net_df in ((consumers, ld), (generators, sg)):
    df["lon"], df["lat"] = net_df.bus.map(lon_of).values, net_df.bus.map(lat_of).values

# --- synthetic supply transformer (PyPSA bus -> SimBench source bus) ----------------
tie = pd.DataFrame([{
    "id": "synthetic-supply-1", "voltage_from": tie_bus.voltage, "voltage_to": src.voltage,
    "distance_km": tie_km, "lon": src.lon, "lat": src.lat}])
topo.to_bus(topo.winding("synthetic-supply-1", "primary", tie_bus.voltage),
            "TransformerWinding", 1, tie_bus.id)
topo.to_bus(topo.winding("synthetic-supply-1", "secondary", src.voltage),
            "TransformerWinding", 1, src.id)

# --- write -------------------------------------------------------------------
for name, df in [("buses", buses), ("switches", switches), ("lines", lines),
                 ("transformers", trafos), ("consumers", consumers),
                 ("generators", generators), ("supply_tie", tie)]:
    df.to_csv(OUT / f"{name}.csv", index=False)
    print(f"{name:16s} {len(df):>6}")
for name, n in topo.write(OUT).items():
    print(f"{name:16s} {n:>6}")
print(f"open switches: {(~switches.closed).sum()}; "
      f"tie: {tie_bus.id} ({tie_bus.voltage} kV) -> {src['name']} ({tie_km} km)")
