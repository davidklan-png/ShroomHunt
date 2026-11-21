#!/usr/bin/env python3
"""Reference geography for the dashboard map (plan §6 MapReference, TASK-003).

Clip Natural Earth 1:10m land to a viewport just beyond the shared mesh
envelope [20,46] × [122,154] (margin 1° by default), simplify per polygon
and emit japan-outline.json — same-origin orientation for the SVG map,
independent of occurrence data, DuckDB and snapshots.

Per ring: Sutherland–Hodgman clip → Douglas–Peucker simplify → drop rings
below the minimum bbox size (--min-size, default 0.04° — islets smaller
than ~4 km render sub-pixel at overview zoom; this lands the output at the
~30.9 KB gzip the plan's §8 scratch build measured, well under the 36 KiB
cap) → round to fixed decimals, dedupe, close. Output bytes are
deterministic: fixed coordinate decimals, stable polygon order, sorted
JSON keys, LF newline.

The input is pinned twice — the v5.1.1 git tag URL below and its sha256 —
and every download is verified against that digest (a cached default-path
copy is re-verified; an explicit --input override is trusted as-is, so
tests can feed synthetic fixtures). The cache lives under
data/raw/naturalearth/ (gitignored, never committed).

Places come from data/reference/map_places.csv: curated bilingual anchors
whose aliases are folded case/diacritic-insensitively by the later place
search (plan §9).
"""
import argparse, csv, gzip, hashlib, json, math, os, shutil, sys, urllib.request

DEFAULT_URL = "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/v5.1.1/geojson/ne_10m_land.geojson"
DEFAULT_SHA256 = "1ac90796408bc6ad6911d69448485d3c4dbf2190370080368a09976e1c9f7416"
DEFAULT_INPUT = "data/raw/naturalearth/ne_10m_land.geojson"
BASE_BOX = (20.0, 46.0, 122.0, 154.0)   # lat_s, lat_n, lon_w, lon_e — shared mesh envelope (plan §6)
ANCHORS = ((26.21, 127.68), (26.7, 128.2))  # Naha, Yanbaru (lat, lon)
ANCHOR_MIN_SPAN = 0.5   # ° an anchor's polygon must span both ways: Okinawa Hontō-scale land
ANCHOR_NEAR = 0.05      # ° an anchor may sit off a surviving coast (Naha is coastal)


class AnchorLostError(Exception):
    """Clip/simplify destroyed Okinawa Hontō-scale land at a required anchor."""


# ---------- geometry ------------------------------------------------------------------------

def _open(ring):
    """GeoJSON rings arrive closed (first == last); simplify works on the open list."""
    return ring[:-1] if len(ring) > 1 and ring[0] == ring[-1] else list(ring)


def _clip_axis(pts, axis, limit, keep_ge):
    """Clip one open ring against a single axis-aligned edge (inside = keep)."""
    def inside(p):
        return p[axis] >= limit if keep_ge else p[axis] <= limit

    def cross(a, b):
        t = (limit - a[axis]) / (b[axis] - a[axis])
        p = [a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])]
        p[axis] = limit
        return p

    if not pts:
        return []
    out, prev = [], pts[-1]
    for cur in pts:
        if inside(cur):
            if not inside(prev):
                out.append(cross(prev, cur))
            out.append(cur)
        elif inside(prev):
            out.append(cross(prev, cur))
        prev = cur
    return out


def clip_ring(pts, s, n, w, e):
    """Sutherland–Hodgman clip of an open ring to the box; cut edges at the border are expected."""
    pts = _clip_axis(pts, 1, s, True)
    pts = _clip_axis(pts, 1, n, False)
    pts = _clip_axis(pts, 0, w, True)
    return _clip_axis(pts, 0, e, False)


def simplify(pts, tol):
    """Douglas–Peucker on an open point list (iterative; distances in raw degrees)."""
    if len(pts) < 3:
        return list(pts)
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        i, j = stack.pop()
        if j <= i + 1:
            continue
        (ax, ay), (bx, by) = pts[i], pts[j]
        dx, dy = bx - ax, by - ay
        seg2 = dx * dx + dy * dy
        far, dist = -1, 0.0
        for k in range(i + 1, j):
            px, py = pts[k]
            if seg2 == 0:
                d = math.hypot(px - ax, py - ay)
            else:
                t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / seg2))
                d = math.hypot(px - (ax + t * dx), py - (ay + t * dy))
            if d > dist:
                dist, far = d, k
        if dist > tol:
            keep[far] = True
            stack.append((i, far))
            stack.append((far, j))
    return [p for p, k in zip(pts, keep) if k]


def ring_bbox(pts):
    """(south, west, north, east) of an open or closed ring."""
    lons = [p[0] for p in pts]
    lats = [p[1] for p in pts]
    return (min(lats), min(lons), max(lats), max(lons))


def point_in_ring(pt, ring):
    """Ray casting over a closed ring."""
    x, y = pt
    inside = False
    for (x1, y1), (x2, y2) in zip(ring, ring[1:]):
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) / (y2 - y1) * (x2 - x1):
            inside = not inside
    return inside


def point_in_polygon(pt, rings):
    """Inside the outer ring and outside every hole."""
    return point_in_ring(pt, rings[0]) and not any(point_in_ring(pt, h) for h in rings[1:])


def distance_to_polygon(pt, rings):
    """Degrees from pt to the nearest segment of any ring."""
    best = float("inf")
    for ring in rings:
        for a, b in zip(ring, ring[1:]):
            dx, dy = b[0] - a[0], b[1] - a[1]
            seg2 = dx * dx + dy * dy
            t = max(0.0, min(1.0, ((pt[0] - a[0]) * dx + (pt[1] - a[1]) * dy) / seg2)) if seg2 else 0.0
            best = min(best, math.hypot(pt[0] - (a[0] + t * dx), pt[1] - (a[1] + t * dy)))
    return best


def _round_closed(pts, decimals):
    """Round to fixed decimals, drop consecutive duplicates, close; None if < 3 points remain."""
    out = []
    for lon, lat in pts:
        p = (round(lon, decimals) + 0.0, round(lat, decimals) + 0.0)  # +0.0 normalises -0.0
        if not out or p != out[-1]:
            out.append(p)
    return [list(p) for p in out + [out[0]]] if len(out) >= 3 else None


def _ring_or_none(ring, box, tolerance, min_size, decimals):
    """Clip → simplify → drop collapsed → round/close one ring; None when nothing survives."""
    clipped = clip_ring(_open(ring), *box)
    if not clipped:
        return None
    simple = simplify(clipped, tolerance)
    s, w, n, e = ring_bbox(simple)
    if max(e - w, n - s) < min_size:  # collapsed below the minimum bbox size
        return None
    return _round_closed(simple, decimals)


def _polygons(geo):
    """Each land polygon (list of rings, outer first) from a FeatureCollection or bare geometry."""
    geoms = [f.get("geometry") or {} for f in geo.get("features", ())] \
        if geo.get("type") == "FeatureCollection" else [geo]
    for g in geoms:
        t = g.get("type")
        if t == "Polygon":
            yield g["coordinates"]
        elif t == "MultiPolygon":
            yield from g["coordinates"]
        elif t is not None:
            raise ValueError(f"unsupported geometry in land input: {t}")


def _require_anchors(polys):
    """Okinawa Hontō-scale land must survive at every anchor — raise instead of shipping a gutted coast."""
    for lat, lon in ANCHORS:
        pt = (lon, lat)
        ok = False
        for rings in polys:
            s, w, n, e = ring_bbox(rings[0])
            if min(n - s, e - w) >= ANCHOR_MIN_SPAN and \
               (point_in_polygon(pt, rings) or distance_to_polygon(pt, rings) <= ANCHOR_NEAR):
                ok = True
                break
        if not ok:
            raise AnchorLostError(
                f"no Okinawa Hontō-scale land survives at anchor ({lat}, {lon}); "
                f"lower --tolerance instead of shipping a silently gutted coast")


# ---------- build ---------------------------------------------------------------------------

def build(geo, place_rows, base_box=BASE_BOX, margin=1.0, tolerance=0.01, min_size=0.04,
          decimals=3, url=DEFAULT_URL):
    """MapReference dict (plan §6) from parsed land GeoJSON and map_places.csv rows."""
    box = (base_box[0] - margin, base_box[1] + margin, base_box[2] - margin, base_box[3] + margin)
    polys = []
    for rings in _polygons(geo):
        # Per-polygon simplify is safe here: Japan's clipped land polygons share no borders
        # with each other (islands, plus the Eurasian mainland cut at the viewport edge),
        # so simplifying each polygon independently cannot open gaps along shared coasts.
        outer = _ring_or_none(rings[0], box, tolerance, min_size, decimals) if rings else None
        if outer is None:
            continue  # no outer ring: no polygon, whatever its holes were
        holes = [h for h in (_ring_or_none(r, box, tolerance, min_size, decimals) for r in rings[1:]) if h]
        polys.append([outer] + holes)
    polys.sort(key=lambda p: ring_bbox(p[0]) + (len(p), sum(len(r) for r in p)))
    _require_anchors(polys)
    places = sorted((place_entry(r, decimals) for r in place_rows), key=lambda p: p["en"])
    return {
        "schema_version": 1,
        "source": {"name": "Natural Earth", "release": "5.1.1", "url": url},
        "bbox": [round(v, 3) + 0.0 for v in box],
        "land": polys,
        "places": places,
    }


def place_entry(row, decimals=3):
    """One §6 places entry from a map_places.csv row (en,ja,aliases,lon,lat,area_id)."""
    out = {"en": row["en"].strip(), "ja": row["ja"].strip(),
           "aliases": sorted({a.strip() for a in (row.get("aliases") or "").split("|") if a.strip()}),
           "at": [round(float(row["lon"]), decimals) + 0.0, round(float(row["lat"]), decimals) + 0.0]}
    if (row.get("area_id") or "").strip():
        out["area_id"] = row["area_id"].strip()
    return out


def read_places(path):
    """Comment-prefixed ('#') CSV of curated anchor rows."""
    with open(path, newline="", encoding="utf-8") as f:
        lines = [ln for ln in f if not ln.lstrip().startswith("#")]
    return list(csv.DictReader(lines))


def load_land(path, url=DEFAULT_URL, sha256=DEFAULT_SHA256):
    """Parse the land GeoJSON at path, downloading the pinned file first when missing."""
    if not os.path.exists(path):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        print(f"downloading {url} -> {path}", file=sys.stderr)
        with urllib.request.urlopen(url, timeout=300) as r, open(path, "wb") as f:
            shutil.copyfileobj(r, f)
    if sha256:
        with open(path, "rb") as f:
            got = hashlib.sha256(f.read()).hexdigest()
        if got != sha256:
            raise SystemExit(f"land input digest mismatch for {path}: {got} != pinned {sha256}")
        print(f"land input {path}: sha256 {got}")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Build japan-outline.json (MapReference, plan §6) from the pinned Natural Earth "
                    "1:10m land GeoJSON — no DuckDB, no occurrence data.")
    ap.add_argument("--input", help=f"land GeoJSON override (digest check skipped; default: {DEFAULT_INPUT})")
    ap.add_argument("--url", default=DEFAULT_URL,
                    help=f"pinned source URL, downloaded when the input is missing and recorded in the "
                         f"output source.url (default: {DEFAULT_URL})")
    ap.add_argument("--places", default="data/reference/map_places.csv", help="curated anchors CSV")
    ap.add_argument("--out", default="japan-outline.json", help="output MapReference JSON path")
    ap.add_argument("--tolerance", type=float, default=0.01,
                    help="Douglas–Peucker tolerance in degrees (default: 0.01; a 0.01° scratch build "
                         "was 30,868 gzip bytes and kept Naha, Yanbaru, Ishigaki, Iriomote)")
    ap.add_argument("--min-size", type=float, default=0.04,
                    help="drop rings whose bbox spans less than this in both directions, in degrees "
                         "(default: 0.04; sub-pixel islets at overview zoom)")
    ap.add_argument("--margin", type=float, default=1.0,
                    help="degrees beyond the [20,46] × [122,154] mesh envelope to clip (default: 1)")
    ap.add_argument("--decimals", type=int, default=3, help="fixed coordinate decimals (default: 3)")
    ap.add_argument("--sha256", default=DEFAULT_SHA256,
                    help="expected sha256 of the pinned land input (default: pinned v5.1.1; empty to skip)")
    args = ap.parse_args(argv)
    geo = load_land(args.input or DEFAULT_INPUT, args.url,
                    "" if args.input else args.sha256)  # explicit --input is trusted as-is (test fixtures)
    ref = build(geo, read_places(args.places), margin=args.margin, tolerance=args.tolerance,
                min_size=args.min_size, decimals=args.decimals, url=args.url)
    data = json.dumps(ref, sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n"
    with open(args.out, "w", newline="\n", encoding="utf-8") as f:
        f.write(data)
    vertices = sum(len(r) for p in ref["land"] for r in p)
    print(f"{args.out}: {len(ref['land'])} land polygons, {vertices} vertices, "
          f"{len(ref['places'])} places, {len(data)} bytes, "
          f"gzip9 {len(gzip.compress(data.encode(), 9))} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
