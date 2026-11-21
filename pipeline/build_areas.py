#!/usr/bin/env python3
"""Build per-area drill-down files for the public dashboard.

Areas: curated islands (data/reference/islands.csv) and, for the four main islands, JIS 1st-mesh tiles
(about 80 km squares) named after their most common prefecture.

For each area: every species recorded there (months, records, ~10 km squares, years, evidence type,
habitat and locality notes) and a ranked list of species that COULD occur there: recorded nearby (Japan
or neighbouring East Asia, up to MAX_KM away) but never in the area. The ranking weights each nearby
record by distance and latitude difference. It is a pointer for fieldwork, not a prediction.

Public-safety rule (D6): Red List and commercially collected species are excluded from every list,
via the shared gate in pipeline/public_taxa.py (TASK-002).

Usage: python3 pipeline/build_areas.py --out data/processed/areas  [--db data/shroomhunt.duckdb]
       [--redlist data/raw/redlist/redlist_fungi_jp.json]
Tables used: jp (GBIF SIMPLE_CSV), optional jp_extra (gbifID, habitat from the DwC-A), optional ea (East Asia cells).
"""
import argparse, csv, json, math, os, re, sys
from collections import Counter, defaultdict
import duckdb

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from geo_ja import (PREFECTURES, norm_pref, haversine_km, neighbour_place,  # noqa: E402
                    cell_key, mesh_cell, mesh_key, tile_id)
from public_taxa import SPECIES_RANKS, load_gate  # noqa: E402

MAX_KM, DECAY_KM, LAT_DECAY = 800, 250, 1.5

# Kind of fungus, from taxonomy: 'm' = mushrooms and other fruiting bodies you can see in the field,
# 'l' = lichens, 'o' = microfungi, yeasts, moulds and DNA-only groups. Approximate by design.
MACRO_CLASSES = {"Agaricomycetes", "Pezizomycetes", "Dacrymycetes"}
MACRO_GENERA = {"Tremella", "Cordyceps", "Ophiocordyceps", "Tolypocladium", "Polycephalomyces", "Xylaria", "Daldinia",
                "Hypoxylon", "Annulohypoxylon", "Kretzschmaria", "Hypomyces", "Leotia", "Cudonia", "Bulgaria",
                "Chlorociboria", "Ascocoryne", "Geoglossum", "Trichoglossum", "Rhytisma"}
LICHEN_CLASSES = {"Lecanoromycetes", "Arthoniomycetes", "Lichinomycetes", "Coniocybomycetes"}
LICHEN_FAMILIES = {"Pyrenulaceae", "Trypetheliaceae", "Verrucariaceae", "Strigulaceae", "Monoblastiaceae",
                   "Arthopyreniaceae", "Collemataceae"}

def kind(cls, fam, genus):
    if cls in LICHEN_CLASSES or fam in LICHEN_FAMILIES: return "l"
    if cls in MACRO_CLASSES or genus in MACRO_GENERA: return "m"
    return "o"

ap = argparse.ArgumentParser()
ap.add_argument("--db", default="data/shroomhunt.duckdb")
ap.add_argument("--out", default="data/processed/areas")
ap.add_argument("--redlist", default="data/raw/redlist/redlist_fungi_jp.json")
ap.add_argument("--mask", default="data/reference/taxon_mask.csv")
ap.add_argument("--coverage", default="data/reference/redlist_coverage.csv")
ap.add_argument("--exceptions", default="data/reference/match_exceptions.csv")
ap.add_argument("--islands", default="data/reference/islands.csv")
a = ap.parse_args()
c = duckdb.connect(a.db)
tables = {r[0] for r in c.execute("select table_name from information_schema.tables").fetchall()}

# ---------- shared protected-taxon gate (TASK-002) -----------------------------------------
gate = load_gate(mask=a.mask, coverage=a.coverage, exceptions=a.exceptions, redlist=a.redlist)

# ---------- base records -------------------------------------------------------------------
habitat_join = "left join jp_extra x on x.gbifID = jp.gbifID" if "jp_extra" in tables else ""
habitat_col = ("concat_ws(' · ', x.habitat, case when x.assoc is null then null "
               "when regexp_matches(lower(x.assoc), '^(on|in|under|with|at|near|from|associated) ') then x.assoc "
               "else 'with ' || x.assoc end), x.elevation"
               if "jp_extra" in tables else "null, null")
rows = c.execute(f"""
  select jp.speciesKey, jp.species, jp.family, jp.taxonRank, jp.basisOfRecord, jp.class, jp.genus,
         try_cast(jp.decimalLatitude as double), try_cast(jp.decimalLongitude as double),
         try_cast(jp.month as int), try_cast(jp.year as int), jp.locality, jp.stateProvince, {habitat_col}
  from jp {habitat_join}""").fetchall()
audit = gate.audit((r[1], r[0], r[6], r[3]) for r in rows)
recs = [r for r in rows if gate.is_public(species=r[1], species_key=r[0], genus=r[6], taxon_rank=r[3])]
masked = len(rows) - audit["public"]

islands = list(csv.DictReader(open(a.islands)))
for i in islands:
    for k in ("lat_min", "lat_max", "lon_min", "lon_max"): i[k] = float(i[k])
    ascii_al = [re.escape(x.lower()) for x in i["aliases"].split("|") if x.isascii()]
    kanji_al = [re.escape(x) for x in i["aliases"].split("|") if not x.isascii()]
    parts = []
    if ascii_al: parts.append(r"\b(?:" + "|".join(ascii_al) + r")\b")
    if kanji_al: parts.append("(?:" + "|".join(kanji_al) + ")")
    i["rx"] = re.compile("|".join(parts))

def island_by_center(row, col):
    """Island owning the cell's centre point, or None (TASK-004).

    Area ownership is per whole cell, never per raw coordinate. islands.csv
    order is the explicit priority list: when boxes overlap at the centre, the
    earlier row wins (ids are unique, so that order is total) — never record
    order. No island claims the centre -> the 1st-mesh tile owns the cell.
    """
    clat, clon = (row + 0.5) / 12, (col + 0.5) / 8
    for i in islands:
        if i["lat_min"] <= clat <= i["lat_max"] and i["lon_min"] <= clon <= i["lon_max"]: return i["id"]
    return None

def island_by_name(loc, sp):
    text = f"{loc or ''} {sp or ''}".lower()
    pref = norm_pref(sp)
    for i in islands:
        if i["rx"].search(text) and (pref is None or pref == i["prefecture"]): return i["id"]
    return None

EVID = {"PRESERVED_SPECIMEN": "s", "MATERIAL_SAMPLE": "d", "LIVING_SPECIMEN": "s", "MATERIAL_CITATION": "s"}
area_recs = defaultdict(list)       # area -> records
fam_class = defaultdict(Counter)
cell_area, tile_prefs = {}, defaultdict(Counter)
for r in recs:
    key, sp, fam, rank, bor, cls, gen, lat, lon, mon, yr, loc, prov, hab, elev = r
    hab = hab or None
    fam_class[fam or ""][cls] += 1
    kd = kind(cls, fam, gen or (sp or "").split(" ")[0])
    cell = mesh_cell(lat, lon)
    if cell is not None:
        aid = island_by_center(*cell) or tile_id(*cell)
        cell_area[cell_key(*cell)] = aid
        how = "c"
        if aid.startswith("t"):
            p = norm_pref(prov)
            if p: tile_prefs[aid][p] += 1
    else:
        aid, how = island_by_name(loc, prov), "n"
        if not aid: continue
    area_recs[aid].append((key, sp, fam, rank, EVID.get(bor, "o"), lat, lon, mon, yr, loc, hab, how, kd, elev))
for i in islands:   # every known island keeps its route even when its eligible records drop to zero
    area_recs.setdefault(i["id"], [])

# ---------- nearby sources for "could occur here" -----------------------------------------
labels = {}
def area_label(aid):
    if aid in labels: return labels[aid]
    i = next((x for x in islands if x["id"] == aid), None)
    if i: lab = (i["name_en"], i["name_ja"])
    else:
        p = sorted(tile_prefs[aid].items(), key=lambda kv: (-kv[1], kv[0]))[0][0] if tile_prefs[aid] else None
        lab = (p or "Japan", PREFECTURES.get(p, "日本"))
    labels[aid] = lab
    return lab

jp_points = defaultdict(lambda: defaultdict(lambda: [0] * 13))   # sp -> (cell centre, label) -> [12 months + total]
for aid, lst in area_recs.items():
    for (key, sp, fam, rank, ev, lat, lon, mon, yr, loc, hab, how, kd, elev) in lst:
        if rank in SPECIES_RANKS and sp and lat is not None:
            cell = mesh_cell(lat, lon)          # one deterministic full-cell centroid per source mesh
            if cell is None: continue
            v = jp_points[(sp, fam or "", kd)][((cell[0] + 0.5) / 12, (cell[1] + 0.5) / 8, aid)]
            v[12] += 1
            if mon and 1 <= mon <= 12: v[mon - 1] += 1
if "nb" in tables:
    for fam, cls, n in c.execute("select family, class, count(*) from nb where family is not null group by 1,2").fetchall():
        fam_class[fam][cls] += n
def fam_kind(fam, sp):
    cls = sorted(fam_class[fam].items(), key=lambda kv: (-kv[1], kv[0]))[0][0] if fam_class.get(fam) else None
    return kind(cls, fam, (sp or "").split(" ")[0])

if "ea" in tables:
    for key, sp, fam, lat, lon, mon, n in c.execute(
            "select speciesKey, species, coalesce(family,''), try_cast(lat as double), try_cast(lon as double), try_cast(\"month\" as int), try_cast(n as int) from ea where species is not null").fetchall():
        if not gate.is_public(species=sp, species_key=key) or lat is None: continue
        v = jp_points[(sp, fam, fam_kind(fam, sp))][(lat, lon, "ea")]
        v[12] += n or 0
        if mon and 1 <= mon <= 12: v[mon - 1] += n or 0

def place_label(aid):
    """Label for where a nearby record is: an island's name, or 'elsewhere in <prefecture>' for a tile."""
    if any(i["id"] == aid for i in islands): return area_label(aid)
    pe, pj = area_label(aid)
    if pe == "Japan": return ("elsewhere in Japan", "国内の他地域")
    suffix = {"Tokyo": "都", "Hokkaido": "", "Osaka": "府", "Kyoto": "府"}.get(pe, "県")
    return (f"elsewhere in {pe}", f"{pj}{suffix}内の他地域")

def possible_for(aid, center, present):
    best = {}
    for (sp, fam, kd), pts in jp_points.items():
        if sp in present: continue
        score, near, months = 0.0, None, [0] * 12
        for (lat, lon, where), v in sorted(pts.items()):   # fixed order: identical float sums under row shuffles
            if where == aid: continue
            d = haversine_km(center[0], center[1], lat, lon)
            if d > MAX_KM: continue
            w = math.log1p(v[12]) * math.exp(-d / DECAY_KM) * math.exp(-abs(lat - center[0]) / LAT_DECAY)
            score += w
            for k in range(12): months[k] += v[k]
            if near is None or d < near[0]:
                near = (d, neighbour_place(lat, lon) if where == "ea" else place_label(where))
        if score > 0 and (sp not in best or score > best[sp][0]): best[sp] = (score, fam, near, months, kd)
    out = []
    for k in ("m", "l", "o"):            # top 40 of each kind, scored against the best of that kind
        top = sorted(((sp, v) for sp, v in best.items() if v[4] == k), key=lambda kv: (-kv[1][0], kv[0]))[:40]
        if not top: continue
        mx = top[0][1][0]
        out += [{"n": sp, "f": fam, "k": kd, "s": max(1, round(100 * sc / mx)), "d": round(near[0]), "w": near[1][0],
                 "wj": near[1][1], "m": months} for sp, (sc, fam, near, months, kd) in top]
    return out

# ---------- write area files ---------------------------------------------------------------
os.makedirs(a.out, exist_ok=True)
index, built = [], {}
for aid, lst in sorted(area_recs.items()):
    isl = next((x for x in islands if x["id"] == aid), None)
    coords = [(r[5], r[6]) for r in lst if r[5] is not None]
    if isl:
        bbox = [isl["lat_min"], isl["lat_max"], isl["lon_min"], isl["lon_max"]]
        name_en, name_ja = isl["name_en"], isl["name_ja"]
        group_en, group_ja = isl["group_en"], isl["group_ja"]
    else:
        p1, u1 = int(aid[1:-2]), int(aid[-2:])
        bbox = [p1 / 1.5, (p1 + 1) / 1.5, u1 + 100, u1 + 101]
        pe, pj = area_label(aid)
        cl, co = (bbox[0] + bbox[1]) / 2, (bbox[2] + bbox[3]) / 2
        name_en = f"{pe} · around {cl:.1f}°N {co:.1f}°E"
        name_ja = f"{pj}・北緯{cl:.1f}度 東経{co:.1f}度付近"
        group_en, group_ja = pe, pj
    center = ((bbox[0] + bbox[1]) / 2, (bbox[2] + bbox[3]) / 2)
    by_sp = {}
    unresolved = 0
    months_all = [0] * 12
    locs = Counter()
    for (key, sp, fam, rank, ev, lat, lon, mon, yr, loc, hab, how, kd, elev) in lst:
        if mon and 1 <= mon <= 12: months_all[mon - 1] += 1
        if loc: locs[loc.strip().rstrip(".")[:60]] += 1
        if rank not in SPECIES_RANKS or not sp:
            unresolved += 1
            continue
        s = by_sp.setdefault(sp, {"n": sp, "f": fam or "", "k": kd, "r": 0, "m": [0] * 12, "sq": set(), "y": [9999, 0],
                                  "e": Counter(), "h": Counter(), "l": Counter(), "nm": 0, "el": []})
        s["r"] += 1
        if mon and 1 <= mon <= 12: s["m"][mon - 1] += 1
        mk = mesh_key(lat, lon)
        if mk is not None: s["sq"].add(mk)
        if yr: s["y"] = [min(s["y"][0], yr), max(s["y"][1], yr)]
        s["e"][ev] += 1
        if hab and len(hab.strip().rstrip(".")) >= 4:
            hk = hab.strip().rstrip(".")[:80]
            s["h"][hk[:1].upper() + hk[1:]] += 1
        if loc: s["l"][loc.strip().rstrip(".")[:60]] += 1
        if how == "n": s["nm"] += 1
        if elev is not None and -50 < elev < 4000: s["el"].append(elev)
    species = []
    for s in sorted(by_sp.values(), key=lambda s: (-s["r"], s["n"])):
        species.append({"n": s["n"], "f": s["f"], "k": s["k"], "r": s["r"], "m": s["m"], "sq": len(s["sq"]),
                        "y": s["y"] if s["y"][1] else None,
                        "e": dict(sorted(s["e"].items(), key=lambda kv: (-kv[1], kv[0]))),
                        "h": [h for h, _ in sorted(s["h"].items(), key=lambda kv: (-kv[1], kv[0]))[:2]],
                        "l": [l for l, _ in sorted(s["l"].items(), key=lambda kv: (-kv[1], kv[0]))[:2]],
                        "nm": s["nm"], "el": [round(min(s["el"])), round(max(s["el"]))] if s["el"] else None})
    out = {"id": aid, "kind": "island" if isl else "tile", "name_en": name_en, "name_ja": name_ja,
           "group_en": group_en, "group_ja": group_ja, "bbox": [round(x, 4) for x in bbox],
           "totals": {"records": len(lst), "by_name": sum(1 for r in lst if r[11] == "n"), "species": len(species),
                      "unresolved": unresolved,
                      "squares": len({k for k in (mesh_key(*p) for p in coords) if k is not None})},
           "months": months_all,
           "top_localities": [(l, n) for l, n in sorted(locs.items(), key=lambda kv: (-kv[1], kv[0]))[:5]],
           # §6: a zero-record entry keeps its route and public reference bbox with empty data
           "species": species, "possible": possible_for(aid, center, set(by_sp)) if lst else []}
    json.dump(out, open(os.path.join(a.out, f"{aid}.json"), "w"), ensure_ascii=False, separators=(",", ":"))
    built[aid] = out
    index.append({"id": aid, "kind": out["kind"], "name_en": name_en, "name_ja": name_ja, "group_en": group_en,
                  "group_ja": group_ja, "bbox": out["bbox"], "records": len(lst), "species": len(species)})
# ---------- island groups: species tagged with the islands they were found on -------------
GROUP_JA = {"Okinawa Prefecture": "沖縄県（全島）"}
groups = defaultdict(list)
for i in islands:
    if i["id"] in built:
        groups[(i["group_en"], i["group_ja"])].append(i["id"])
        if i["prefecture"] == "Okinawa": groups[("Okinawa Prefecture", GROUP_JA["Okinawa Prefecture"])].append(i["id"])
for (gen, gja), members in sorted(groups.items()):
    if len(members) < 2: continue
    gid = "g-" + re.sub(r"[^a-z0-9]+", "-", gen.lower()).strip("-")
    sp = {}
    for mid in members:
        for x in built[mid]["species"]:
            t = sp.setdefault(x["n"], {"n": x["n"], "f": x["f"], "k": x["k"], "r": 0, "m": [0] * 12, "sq": 0, "y": None,
                                      "e": Counter(), "h": [], "l": [], "nm": 0, "i": [], "el": None})
            t["r"] += x["r"]; t["sq"] += x["sq"]; t["nm"] += x["nm"]; t["i"].append(mid)
            t["m"] = [p + q for p, q in zip(t["m"], x["m"])]
            if x["y"]: t["y"] = [min(t["y"][0], x["y"][0]), max(t["y"][1], x["y"][1])] if t["y"] else list(x["y"])
            t["e"].update(x["e"]); t["h"] = (t["h"] + x["h"])[:2]; t["l"] = (t["l"] + x["l"])[:2]
            if x.get("el"): t["el"] = [min(t["el"][0], x["el"][0]), max(t["el"][1], x["el"][1])] if t["el"] else list(x["el"])
    species = sorted(({**v, "e": dict(v["e"])} for v in sp.values()), key=lambda v: (-v["r"], v["n"]))
    poss = {}
    for mid in members:
        for x in built[mid]["possible"]:
            if x["n"] not in sp and (x["n"] not in poss or x["s"] > poss[x["n"]]["s"]): poss[x["n"]] = {**x, "i": mid}
    possible = sorted(poss.values(), key=lambda v: (v["k"], -v["s"], v["n"]))
    bb = [min(built[m]["bbox"][0] for m in members), max(built[m]["bbox"][1] for m in members),
          min(built[m]["bbox"][2] for m in members), max(built[m]["bbox"][3] for m in members)]
    tot = {k: sum(built[m]["totals"][k] for m in members) for k in ("records", "by_name", "unresolved", "squares")}
    tot["species"] = len(species)
    out = {"id": gid, "kind": "group", "members": members, "name_en": gen, "name_ja": gja, "group_en": gen, "group_ja": gja,
           "bbox": bb, "totals": tot, "months": [sum(built[m]["months"][k] for m in members) for k in range(12)],
           "top_localities": [], "species": species, "possible": possible,
           "member_names": {m: [built[m]["name_en"], built[m]["name_ja"]] for m in members}}
    json.dump(out, open(os.path.join(a.out, f"{gid}.json"), "w"), ensure_ascii=False, separators=(",", ":"))
    index.append({"id": gid, "kind": "group", "name_en": gen, "name_ja": gja, "group_en": gen, "group_ja": gja,
                  "bbox": bb, "records": tot["records"], "species": len(species), "members": members})

json.dump({"areas": index, "cells": dict(sorted(cell_area.items())), "masked_records": masked,
           "has_habitat": "jp_extra" in tables, "has_neighbours": "ea" in tables},
          open(os.path.join(a.out, "index.json"), "w"), ensure_ascii=False, separators=(",", ":"))
print(f"{len(index)} areas ({sum(1 for x in index if x['kind']=='island')} islands), {len(cell_area)} squares mapped, "
      f"{masked} sensitive records excluded, habitat={'jp_extra' in tables}, neighbours={'ea' in tables}")
print(f"gate audit (aggregate): {audit}")
