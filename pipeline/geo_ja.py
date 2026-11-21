"""Shared geography helpers: prefecture names, name normalisation, haversine, JIS mesh."""
import json, math, re

PREFECTURES = {  # romaji -> kanji
 "Hokkaido": "北海道", "Aomori": "青森", "Iwate": "岩手", "Miyagi": "宮城", "Akita": "秋田", "Yamagata": "山形",
 "Fukushima": "福島", "Ibaraki": "茨城", "Tochigi": "栃木", "Gunma": "群馬", "Saitama": "埼玉", "Chiba": "千葉",
 "Tokyo": "東京", "Kanagawa": "神奈川", "Niigata": "新潟", "Toyama": "富山", "Ishikawa": "石川", "Fukui": "福井",
 "Yamanashi": "山梨", "Nagano": "長野", "Gifu": "岐阜", "Shizuoka": "静岡", "Aichi": "愛知", "Mie": "三重",
 "Shiga": "滋賀", "Kyoto": "京都", "Osaka": "大阪", "Hyogo": "兵庫", "Nara": "奈良", "Wakayama": "和歌山",
 "Tottori": "鳥取", "Shimane": "島根", "Okayama": "岡山", "Hiroshima": "広島", "Yamaguchi": "山口",
 "Tokushima": "徳島", "Kagawa": "香川", "Ehime": "愛媛", "Kochi": "高知", "Fukuoka": "福岡", "Saga": "佐賀",
 "Nagasaki": "長崎", "Kumamoto": "熊本", "Oita": "大分", "Miyazaki": "宮崎", "Kagoshima": "鹿児島", "Okinawa": "沖縄"}
_KANJI = {v: k for k, v in PREFECTURES.items()}
_ALT = {"hokkaidou": "Hokkaido", "hyougo": "Hyogo", "kouchi": "Kochi", "ooita": "Oita", "tokyo-to": "Tokyo",
        "kyouto": "Kyoto", "oosaka": "Osaka", "gumma": "Gunma"}

def norm_pref(s):
    """'Kanagawa Prefecture', 'Kanagawa Pref.', 'kanagawa-ken', '神奈川県' -> 'Kanagawa' (or None)."""
    if not s: return None
    t = s.strip()
    for k, v in _KANJI.items():
        if t.startswith(k): return v
    t = re.sub(r"(?i)[\s\-]*(prefecture|pref\.?|ken|fu|to|do)\s*$", "", t).strip()
    t = re.sub(r"(?i)^(hokkai)$", "hokkaido", t)
    low = t.lower().replace("ō", "o").replace("ū", "u")
    if low in _ALT: return _ALT[low]
    for k in PREFECTURES:
        if low == k.lower(): return k
    return None

def haversine_km(lat1, lon1, lat2, lon2):
    p = math.pi / 180
    a = math.sin((lat2 - lat1) * p / 2) ** 2 + math.cos(lat1 * p) * math.cos(lat2 * p) * math.sin((lon2 - lon1) * p / 2) ** 2
    return 12742 * math.asin(math.sqrt(a))

def neighbour_place(lat, lon):
    """Rough label for a neighbouring-country 1-degree cell."""
    if 21.5 <= lat <= 25.6 and 119.3 <= lon <= 122.3: return ("Taiwan", "台湾")
    if 33 <= lat <= 38.7 and 124 <= lon <= 131: return ("Korea", "朝鮮半島")
    if lat >= 42 and lon >= 130: return ("Russian Far East", "ロシア極東")
    return ("China", "中国")

# ---------- JIS X 0410 2nd mesh: the one canonical geometry (TASK-004) ----------------------
# row/col are integer cell coordinates; every public artefact (keys, centres,
# rectangles, tile ids, the JS parity fixture) derives from them. No builder
# computes floor(lat*12) on its own any more.
MESH_LAT_CELLS, MESH_LON_CELLS = 12, 8   # cells per degree: 5' latitude, 7.5' longitude
MESH_EPS = 1e-9                           # snaps float noise just below a cell edge into that cell
MESH_ENVELOPE = (20, 46, 122, 154)        # half-open [south, north) x [west, east)
ROW_MIN, ROW_MAX = MESH_ENVELOPE[0] * MESH_LAT_CELLS, MESH_ENVELOPE[1] * MESH_LAT_CELLS  # 240 <= row < 552
COL_MIN, COL_MAX = MESH_ENVELOPE[2] * MESH_LON_CELLS, MESH_ENVELOPE[3] * MESH_LON_CELLS  # 976 <= col < 1232

def mesh_cell(lat, lon):
    """(row, col) of the JIS 2nd mesh cell containing a finite point, else None.

    row = floor(lat * 12 + 1e-9), col = floor(lon * 8 + 1e-9); the whole cell
    must lie in the half-open envelope [20,46) x [122,154), so the north and
    east edges (46, 154) are rejected while exact south/west edges (20, 122)
    belong to the first cell.
    """
    if lat is None or lon is None or not (math.isfinite(lat) and math.isfinite(lon)):
        return None
    row = math.floor(lat * MESH_LAT_CELLS + MESH_EPS)
    col = math.floor(lon * MESH_LON_CELLS + MESH_EPS)
    if not (ROW_MIN <= row < ROW_MAX and COL_MIN <= col < COL_MAX):
        return None
    return row, col

def mesh_key(lat, lon):
    """'row:col' MeshKey of a point, or None when the point is rejected."""
    cell = mesh_cell(lat, lon)
    return None if cell is None else cell_key(*cell)

def cell_key(row, col):
    return f"{row}:{col}"

def cell_rect(row, col):
    """[south, north, west, east] edges, exact from the integer key alone (Box order)."""
    return [row / MESH_LAT_CELLS, (row + 1) / MESH_LAT_CELLS, col / MESH_LON_CELLS, (col + 1) / MESH_LON_CELLS]

def cell_center(row, col):
    """Public cell centre rounded to 4 decimals, [lat, lon] (MeshRow order)."""
    return [round((row + 0.5) / MESH_LAT_CELLS, 4), round((col + 0.5) / MESH_LON_CELLS, 4)]

def tile_id(row, col):
    """JIS 1st mesh (~80 km) tile id: one tile is exactly 8x8 2nd-mesh cells."""
    return f"t{row // 8}{col // 8 - 100:02d}"

# Parity points for the Bio_HP JS port of this geometry: exact envelope edges,
# just-outside neighbours, float-edge snaps, within-cell moves and interior
# points. The fixture is generated from the module; never hand-edit it.
_PARITY_POINTS = [
    (20.0, 122.0),                   # exact south/west edges: first cell of the envelope
    (19.9999999, 122.0),             # just south of the envelope: rejected
    (46.0, 122.0),                   # north edge is half-open: rejected
    (45.9999999, 122.0),             # top row
    (20.0, 154.0),                   # east edge is half-open: rejected
    (20.0, 153.9999999),             # easternmost column
    (26.5, 127.875),                 # exact float edges: 26.5*12 and 127.875*8 are integers
    (26.51, 127.9),                  # same cell as above: within-cell moves change nothing
    (26.4583, 127.9375),             # published centre lands back in its own cell
    (26.083333333333332, 127.875),   # double just below the 313/12 edge: epsilon snaps to row 313
    (35.6812, 139.7671),             # interior point (Tokyo)
]

def boundary_fixture():
    points = []
    for lat, lon in _PARITY_POINTS:
        cell = mesh_cell(lat, lon)
        points.append({"lat": lat, "lon": lon,
                       "row": None if cell is None else cell[0],
                       "col": None if cell is None else cell[1],
                       "key": None if cell is None else cell_key(*cell),
                       "center": None if cell is None else cell_center(*cell),
                       "rect": None if cell is None else cell_rect(*cell)})
    return {"note": "Generated by pipeline/geo_ja.py ('python3 pipeline/geo_ja.py <path>'); never "
                    "hand-edit. Null geometry marks a rejected point: non-finite, or outside the "
                    "half-open envelope [20,46) x [122,154).",
            "row_per_deg": MESH_LAT_CELLS, "col_per_deg": MESH_LON_CELLS, "epsilon": MESH_EPS,
            "envelope": list(MESH_ENVELOPE), "points": points}

def write_boundary_fixture(path):
    with open(path, "w") as f:
        json.dump(boundary_fixture(), f, indent=1)
        f.write("\n")

if __name__ == "__main__":
    import sys
    write_boundary_fixture(sys.argv[1] if len(sys.argv) > 1 else "tests/fixtures/mesh_boundary.json")
