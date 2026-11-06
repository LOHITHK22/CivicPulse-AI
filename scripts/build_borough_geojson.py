"""Build the simplified borough boundary file used by the Geographic Intelligence map.

Source: NYC Department of City Planning, "Borough Boundaries" (water areas excluded), published on NYC
Open Data as dataset ``gthc-hcne`` (GeoJSON, WGS84 / EPSG:4326). The script downloads the source,
simplifies each ring with Ramer-Douglas-Peucker (tolerance in degrees), drops rings smaller than a
minimum area, rounds coordinates to 5 decimals (~1 m), computes a label point inside each borough and
writes ``apps/web/public/geo/nyc-boroughs.geojson`` plus a provenance record. Boundaries are for display
only; complaint counts are joined on the borough name recorded in the 311 data, never spatially.

Usage: python scripts/build_borough_geojson.py [--source path/to/boroughs.geojson] [--tolerance 0.0004]
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "apps" / "web" / "public" / "geo" / "nyc-boroughs.geojson"
PROVENANCE = ROOT / "docs" / "data" / "borough_boundaries.json"
SOURCE_URL = "https://data.cityofnewyork.us/resource/gthc-hcne.geojson"
META_URL = "https://data.cityofnewyork.us/api/views/gthc-hcne.json"
IDS = {
    "Bronx": "bronx",
    "Brooklyn": "brooklyn",
    "Manhattan": "manhattan",
    "Queens": "queens",
    "Staten Island": "staten_island",
}
Point = tuple[float, float]


def _perp(p: Point, a: Point, b: Point) -> float:
    (x, y), (x1, y1), (x2, y2) = p, a, b
    dx, dy = x2 - x1, y2 - y1
    if dx == 0 and dy == 0:
        return float(((x - x1) ** 2 + (y - y1) ** 2) ** 0.5)
    return float(abs(dy * x - dx * y + x2 * y1 - y2 * x1) / (dx * dx + dy * dy) ** 0.5)


def rdp(points: list[Point], tol: float) -> list[Point]:
    """Iterative Ramer-Douglas-Peucker (no recursion limit issues on long coastlines)."""
    if len(points) < 3:
        return points
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        s, e = stack.pop()
        best, idx = 0.0, -1
        for i in range(s + 1, e):
            d = _perp(points[i], points[s], points[e])
            if d > best:
                best, idx = d, i
        if best > tol and idx > 0:
            keep[idx] = True
            stack += [(s, idx), (idx, e)]
    return [p for p, k in zip(points, keep, strict=True) if k]


def ring_area(ring: list[Point]) -> float:
    return 0.5 * sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1], strict=True))


def centroid(ring: list[Point]) -> Point:
    a = ring_area(ring)
    cx = cy = 0.0
    for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1], strict=True):
        f = x1 * y2 - x2 * y1
        cx += (x1 + x2) * f
        cy += (y1 + y2) * f
    return (cx / (6 * a), cy / (6 * a))


def inside(p: Point, ring: list[Point]) -> bool:
    x, y = p
    res = False
    for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1], strict=True):
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            res = not res
    return res


def label_point(ring: list[Point]) -> Point:
    """Centroid if it lies inside the ring, else the midpoint of the widest horizontal chord at centroid y."""
    c = centroid(ring)
    if inside(c, ring):
        return c
    y = c[1]
    xs = sorted(
        x1 + (y - y1) * (x2 - x1) / (y2 - y1)
        for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1], strict=True)
        if (y1 > y) != (y2 > y)
    )
    pairs = list(zip(xs[::2], xs[1::2], strict=False))
    a, b = max(pairs, key=lambda t: t[1] - t[0])
    return ((a + b) / 2, y)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="")
    ap.add_argument("--tolerance", type=float, default=0.0004)
    ap.add_argument("--min-area", type=float, default=2e-6, help="drop rings smaller than this (square degrees)")
    a = ap.parse_args()
    if a.source:
        raw = Path(a.source).read_bytes()
    else:
        with urllib.request.urlopen(SOURCE_URL, timeout=60) as r:  # fixed https URL
            raw = r.read()
    meta: dict[str, Any] = {}
    try:
        with urllib.request.urlopen(META_URL, timeout=30) as r:  # fixed https URL
            meta = json.loads(r.read())
    except OSError:
        pass
    src = json.loads(raw)
    features: list[dict[str, Any]] = []
    for feat in src["features"]:
        props = feat["properties"]
        name = props["boroname"]
        polys = feat["geometry"]["coordinates"]
        out_polys = []
        largest: tuple[float, list[Point]] = (0.0, [])
        for poly in polys:
            rings = []
            for i, ring in enumerate(poly):
                pts = [(float(x), float(y)) for x, y in ring]
                simp = rdp(pts, a.tolerance)
                area = abs(ring_area(simp[:-1])) if len(simp) > 3 else 0.0
                if len(simp) < 4 or (area < a.min_area):
                    if i == 0:
                        break  # outer ring too small: drop the whole polygon
                    continue
                rings.append([[round(x, 5), round(y, 5)] for x, y in simp])
                if i == 0 and area > largest[0]:
                    largest = (area, simp[:-1])
            if rings:
                out_polys.append(rings)
        lx, ly = label_point(largest[1])
        features.append(
            {
                "type": "Feature",
                "properties": {
                    "borough_id": IDS[name],
                    "borough": name.upper(),
                    "borocode": int(props["borocode"]),
                    "label_lon": round(lx, 5),
                    "label_lat": round(ly, 5),
                },
                "geometry": {"type": "MultiPolygon", "coordinates": out_polys},
            }
        )
    features.sort(key=lambda f: f["properties"]["borocode"])
    fc = {"type": "FeatureCollection", "features": features}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(fc, separators=(",", ":"))
    OUT.write_text(text, encoding="utf-8", newline="\n")
    rows_updated = meta.get("rowsUpdatedAt")
    prov = {
        "name": "Borough Boundaries (water areas excluded)",
        "publisher": "NYC Department of City Planning (DCP)",
        "open_data_id": "gthc-hcne",
        "source_url": SOURCE_URL,
        "landing_page": "https://data.cityofnewyork.us/d/gthc-hcne",
        "attribution": meta.get("attribution") or "Department of City Planning (DCP)",
        "attribution_link": meta.get("attributionLink"),
        "license_field": meta.get("license"),
        "terms": "NYC Open Data Terms of Use (https://www.nyc.gov/home/terms-of-use.page); no explicit license field.",
        "source_rows_updated_at_utc": dt.datetime.fromtimestamp(rows_updated, dt.UTC).isoformat()
        if rows_updated
        else None,
        "crs": "EPSG:4326 (WGS84 longitude/latitude), as published",
        "downloaded_at_utc": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "source_sha256": hashlib.sha256(raw).hexdigest(),
        "source_bytes": len(raw),
        "simplification": {
            "method": "Ramer-Douglas-Peucker per ring",
            "tolerance_degrees": a.tolerance,
            "min_ring_area_sq_degrees": a.min_area,
            "coordinate_decimals": 5,
        },
        "output": str(OUT.relative_to(ROOT)).replace("\\", "/"),
        "output_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "output_bytes": len(text),
        "use": "Display only. Complaint counts are aggregated by the borough recorded in each 311 request; "
        "no spatial join, no individual locations.",
    }
    PROVENANCE.write_text(json.dumps(prov, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {OUT} ({len(text):,} bytes) from {len(raw):,} source bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
