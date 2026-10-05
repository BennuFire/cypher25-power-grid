"""Fetch current OpenStreetMap data for the OSM objects behind PyPSA substations.

Reads osm_ids from data/import/transmission/substations.csv (run prepare_transmission.py first)
and caches, so prepare_transmission.py stays reproducible offline:
  1. tags of every object        -> data/raw/osm/substation_tags.csv
     (many virtual_* PyPSA buses are built from power=line ways, not substations)
  2. outlines of power=substation -> data/raw/osm/substation_geometry.csv
     `parts` is JSON: a list of [lon, lat] coordinate lists — the closed way itself, or a
     relation's outer member ways (which may be fragments of a ring).
Uses public Overpass API servers — keep the pauses polite. Both passes are resumable: ids
already cached are skipped. Data © OpenStreetMap contributors, ODbL.
"""
import http.client
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "data" / "raw" / "osm"
TAGS_CSV = CACHE / "substation_tags.csv"
GEOM_CSV = CACHE / "substation_geometry.csv"
# Mirrors (e.g. overpass.private.coffee, overpass.kumi.systems) can be added here; on
# 2026-10-04 they did not respond, and overpass-api.de allows 2 parallel queries per client
SERVERS = ["https://overpass-api.de/api/interpreter"]
BATCH = 300
GEOM_BATCH = 75        # outlines are far larger responses than tags
PAUSE_S = 10
RETRIES = 8
DEADLINE_S = 240
TAGS = ["power", "name", "name:en", "operator", "ref", "voltage", "substation"]


def query(kind, ids, out):
    q = f"[out:json][timeout:180];{kind}(id:{','.join(ids)});out {out};"
    body = urllib.parse.urlencode({"data": q}).encode()
    for attempt in range(RETRIES):
        server = SERVERS[attempt % len(SERVERS)]
        req = urllib.request.Request(server, data=body,
                                     headers={"User-Agent": "electric-grid-neo4j-blog/1.0"})
        try:
            # urlopen's timeout is per socket read; a server trickling bytes can stall forever,
            # so also enforce a deadline for the whole response
            deadline = time.monotonic() + DEADLINE_S
            with urllib.request.urlopen(req, timeout=60) as r:
                chunks = []
                while chunk := r.read(65536):
                    chunks.append(chunk)
                    if time.monotonic() > deadline:
                        raise TimeoutError(f"response took over {DEADLINE_S}s")
            return json.loads(b"".join(chunks))["elements"]
        # OSError covers HTTP errors, timeouts and resets; HTTPException covers cut-off
        # responses (IncompleteRead); ValueError covers truncated JSON
        except (OSError, http.client.HTTPException, ValueError) as e:
            wait = 15 * (attempt + 1)
            print(f"  {server.split('/')[2]}: {e}; retrying in {wait}s")
            time.sleep(wait)
    raise RuntimeError(f"Overpass failed {RETRIES} times for a {kind} batch")


def batches(ids, cache, size=BATCH):
    """Yield (kind, numeric ids) batches for ids not yet in the cache file."""
    done = set(pd.read_csv(cache).osm_id) if cache.exists() else set()
    todo = pd.Series(sorted(set(ids) - done), dtype=str)
    for kind in ("way", "relation"):
        nums = todo[todo.str.startswith(kind + "/")].str.split("/").str[1].tolist()
        for i in range(0, len(nums), size):
            yield kind, nums[i:i + size]
            print(f"  {kind}: {min(i + size, len(nums))}/{len(nums)}")
            time.sleep(PAUSE_S)


def append(rows, cache):
    pd.DataFrame(rows).to_csv(cache, mode="a", header=not cache.exists(), index=False)


def parts(element):
    if element["type"] == "way":
        return [[[p["lon"], p["lat"]] for p in element.get("geometry", [])]]
    return [[[p["lon"], p["lat"]] for p in m["geometry"]]
            for m in element.get("members", [])
            if m.get("role") == "outer" and m.get("geometry")]


def fetch_tags(osm_ids):
    print("pass 1: tags")
    for kind, nums in batches(osm_ids, TAGS_CSV):
        found = {e["id"]: e.get("tags", {}) for e in query(kind, nums, "tags")}
        # ids OSM no longer has are cached with found=False, so reruns skip them
        append([{"osm_id": f"{kind}/{n}", "found": int(n) in found,
                 **{t: found.get(int(n), {}).get(t) for t in TAGS}} for n in nums], TAGS_CSV)


def fetch_geometry():
    print("pass 2: substation outlines")
    tags = pd.read_csv(TAGS_CSV)
    for kind, nums in batches(tags.osm_id[tags.power == "substation"], GEOM_CSV, GEOM_BATCH):
        found = {e["id"]: parts(e) for e in query(kind, nums, "geom")}
        append([{"osm_id": f"{kind}/{n}", "parts": json.dumps(found.get(int(n), []))}
                for n in nums], GEOM_CSV)


def main():
    subs = pd.read_csv(ROOT / "data" / "import" / "transmission" / "substations.csv")
    osm_ids = subs.osm_ids.str.split(";").explode().unique()
    CACHE.mkdir(parents=True, exist_ok=True)
    fetch_tags(osm_ids)
    fetch_geometry()
    tags, geom = pd.read_csv(TAGS_CSV), pd.read_csv(GEOM_CSV)
    print(f"tags: {len(tags)} of {len(osm_ids)} ids cached, {(~tags.found).sum()} gone from OSM; "
          f"outlines: {len(geom)} cached, {(geom.parts == '[]').sum()} empty")


if __name__ == "__main__":
    main()
