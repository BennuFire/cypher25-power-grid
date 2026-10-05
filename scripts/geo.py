"""Geometry helpers shared by the prepare scripts. Coordinates are WGS84 degrees."""
import numpy as np

EARTH_KM = 6371.0


def distances(lat, lon, ref_lat, ref_lon):
    """Haversine distance matrix (km): one row per point, one column per reference point."""
    a, o = np.radians(lat)[:, None], np.radians(lon)[:, None]
    rlat, rlon = np.radians(ref_lat), np.radians(ref_lon)
    h = np.sin((rlat - a) / 2) ** 2 + np.cos(a) * np.cos(rlat) * np.sin((rlon - o) / 2) ** 2
    return 2 * EARTH_KM * np.arcsin(np.sqrt(h))


def nearest(lat, lon, ref_lat, ref_lon, chunk=2000):
    """Index of and haversine distance (km) to the nearest reference point."""
    idx, dist = [], []
    for s in range(0, len(lat), chunk):
        d = distances(lat[s:s + chunk], lon[s:s + chunk], ref_lat, ref_lon)
        i = d.argmin(axis=1)
        idx.append(i)
        dist.append(d[np.arange(len(i)), i])
    return np.concatenate(idx), np.concatenate(dist)


def convex_hull(points):
    """Closed convex hull ring of [lon, lat] points (Andrew's monotone chain)."""
    pts = sorted(set(map(tuple, points)))
    if len(pts) < 3:
        return [list(p) for p in pts]

    def half(seq):
        h = []
        for p in seq:
            while len(h) >= 2 and ((h[-1][0] - h[-2][0]) * (p[1] - h[-2][1])
                                   - (h[-1][1] - h[-2][1]) * (p[0] - h[-2][0])) <= 0:
                h.pop()
            h.append(p)
        return h[:-1]

    ring = half(pts) + half(pts[::-1])
    return [list(p) for p in ring + ring[:1]]


def ring_area_m2(ring):
    """Area of a closed [lon, lat] ring (shoelace on a local equirectangular projection)."""
    a = np.radians(np.array(ring))
    x = a[:, 0] * EARTH_KM * 1000 * np.cos(a[:, 1].mean())
    y = a[:, 1] * EARTH_KM * 1000
    return abs(np.dot(x[:-1], y[1:]) - np.dot(x[1:], y[:-1])) / 2
