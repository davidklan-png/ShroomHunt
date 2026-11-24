#!/usr/bin/env python3
"""Build per-area drill-down files for the public dashboard.

Areas: curated islands (data/reference/islands.csv) and, for the four main islands, JIS 1st-mesh tiles
(about 80 km squares) named after their most common prefecture.

For each area: every species recorded there (months, records, ~10 km squares, years, evidence type)
and a ranked list of species that COULD occur there: recorded nearby (Japan or neighbouring East
Asia, up to MAX_KM away) but never in the area. The ranking weights each nearby record by distance
and latitude difference. It is a pointer for fieldwork, not a prediction.

Public-safety rule (D6): Red List and commercially collected species are excluded from every list,
via the shared gate in pipeline/public_taxa.py (TASK-002).

Public-safety rule (§6/§7, TASK-005): no occurrence-derived detail survives in the public files.
Habitat/host prose, locality text and elevation are withdrawn (h/l/el empty, top_localities empty,
by_name 0); area tables, months and candidate geography derive only from eligible records placed
on whole JIS cells; candidate distances are 50 km bands between a public area centre and snapped
cell centroids (East Asia keeps its native 1° cells). The source-precision eligibility itself is
shared with build_dashboard_data.py via pipeline/eligibility.py (TASK-006), and §6 v1 files carry
schema_version 1 plus a bundle snapshot stamped by pipeline/snapshot.py.

Fruiting-evidence rule (TASK-012): every month histogram (area months, per-species m, candidate m)
counts non-'d' evidence only (basisOfRecord MATERIAL_SAMPLE excluded); record totals, squares,
years and evidence breakdowns keep every record. Each area may also carry a non-locational
dominant_source [dataset title, share] when one datasetKey holds >= 30% of its records.

Cross-dataset dedup (TASK-014): records sharing a normalised (institutionCode, catalogNumber) —
one physical specimen published by two datasets under different gbifIDs — count once, the first
in gbifID order winning; an empty catalogNumber identifies nothing, so those rows each count.

Overview aggregates (TASK-021): every area (and every island group, summing its
members' arrays) also ships evidence ([basisOfRecord, n], n desc), id_depth (the
species/genus/higher buckets of pipeline/taxonomy.py) and decades ([decade, n]
ascending, 1850-2100 window, year-less records left out) over exactly the record
set totals.records counts — the gated, area-assigned set, name-routed records
included, masked records never.

COL XR identity (TASK-018): species identity is coalesce(col_key, 'L'||speciesKey) and the
display name coalesce(col_name, species); genus/family/order/class come from the COL columns
of the accepted taxon, null falling back to the backbone. Species rows and candidates carry
the COL key as the additive "c" field (null when unmapped). A DB that carries COL keys
requires data/reference/col_mask_map.csv (fail closed, pipeline/col_xr.py).

Legacy backbone key (TASK-021): species and candidate rows also carry "lk" — the legacy
speciesKey as a string — set ONLY when the row has no COL key: a lumped species with a "c"
fires the primary /taxon/ link and needs no fallback, so merged rows keep lk null.

Usage: python3 pipeline/build_areas.py --out data/processed/areas  [--db data/shroomhunt.duckdb]
       [--redlist data/raw/redlist/redlist_fungi_jp.json]
Tables used: jp (GBIF SIMPLE_CSV), optional jp_extra (gbifID, habitat and the DwC-A precision notes),
optional ea (East Asia cells).
"""
import argparse, csv, json, math, os, re, sys, urllib.request
from collections import Counter, defaultdict
import duckdb

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from geo_ja import (PREFECTURES, norm_pref, haversine_km, neighbour_place,  # noqa: E402
                    cell_key, cell_center, mesh_cell, tile_id,
                    MESH_LAT_CELLS, MESH_LON_CELLS, MESH_EPS, ROW_MIN, ROW_MAX, COL_MIN, COL_MAX)
from public_taxa import SPECIES_RANKS, load_gate  # noqa: E402
from eligibility import precision_selection, spatially_eligible  # noqa: E402
from snapshot import write_json  # noqa: E402
from shared import kind, specimen_key  # noqa: E402
from col_xr import exprs, keyed  # noqa: E402
from taxonomy import id_depth_bucket  # noqa: E402

MAX_KM, DECAY_KM, LAT_DECAY = 800, 250, 1.5
BAND_KM, BAND_CAP = 50, 250   # §6: candidate d is the lower edge of a 50 km band (250 = 250 km+)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/shroomhunt.duckdb")
    ap.add_argument("--out", default="data/processed/areas")
    ap.add_argument("--redlist", default="data/raw/redlist/redlist_fungi_jp.json")
    ap.add_argument("--mask", default="data/reference/taxon_mask.csv")
    ap.add_argument("--coverage", default="data/reference/redlist_coverage.csv")
    ap.add_argument("--exceptions", default="data/reference/match_exceptions.csv")
    ap.add_argument("--col-mask-map", default="data/reference/col_mask_map.csv")
    ap.add_argument("--islands", default="data/reference/islands.csv")
    ap.add_argument("--titles", help="optional JSON {datasetKey: title} resolving dominant_source "
                                     "titles offline (tests); misses still go to the GBIF API")
    a = ap.parse_args()
    offline_titles = json.load(open(a.titles)) if a.titles else {}
    _titles = {}   # per-run cache: datasetKey -> resolved title

    def dataset_title(key):
        """datasetKey -> public title: the offline --titles map first, then the GBIF dataset
        API (api.gbif.org/v1/dataset/{key}); the bare datasetKey on any failure."""
        if key in _titles: return _titles[key]
        title = offline_titles.get(key)
        if title is None:
            try:
                with urllib.request.urlopen(f"https://api.gbif.org/v1/dataset/{key}", timeout=10) as r:
                    title = json.load(r).get("title")
            except Exception as e:
                print(f"areas: dataset {key}: title fetch failed ({e}); shipping the bare datasetKey",
                      file=sys.stderr)
        _titles[key] = title or key
        return _titles[key]

    c = duckdb.connect(a.db)
    tables = {r[0] for r in c.execute("select table_name from information_schema.tables").fetchall()}

    # ---------- shared protected-taxon gate (TASK-002) -----------------------------------------
    # TASK-018: a DB that carries COL keys gates on them too, and requires col_mask_map.
    gate = load_gate(mask=a.mask, coverage=a.coverage, exceptions=a.exceptions, redlist=a.redlist,
                     col_mask=a.col_mask_map if keyed(c, "jp") else None)

    # ---------- source-precision eligibility (§7) ------------------------------------------------
    # Shared with build_dashboard_data.py since TASK-006 (pipeline/eligibility.py), so a cell
    # held only by flagged/coarsened records can appear in neither output, never in just one.
    elig_join, sel = precision_selection(c)

    # ---------- base records -------------------------------------------------------------------
    # institutionCode/catalogNumber are header-guarded like the research builder's columns: a jp
    # table without them (test fixtures) skips cross-dataset dedup instead of erroring. The
    # gbifID order pins which duplicate survives (the first), keeping output deterministic.
    jp_cols = {r[0] for r in c.execute(
        "select column_name from information_schema.columns where table_name='jp'").fetchall()}
    X = exprs("jp", jp_cols)   # TASK-018: name/classification fall back to the backbone columns
    specimen_cols = ", ".join(f"jp.{n}" if n in jp_cols else "null" for n in ("institutionCode", "catalogNumber"))
    rows = c.execute(f"""
      select jp.speciesKey, {X['sp_name']}, {X['family']}, jp.taxonRank, jp.basisOfRecord,
             {X['class']}, {X['genus']},
             try_cast(jp.decimalLatitude as double), try_cast(jp.decimalLongitude as double),
             try_cast(jp.month as int), try_cast(jp.year as int), jp.locality, jp.stateProvince,
             jp.datasetKey, {specimen_cols}, {", ".join(sel)}, {X['sp_key']}, {X['col_key']}
      from jp {elig_join}""" + (" order by jp.gbifID nulls last" if "gbifID" in jp_cols else "")).fetchall()
    audit = gate.audit((r[1], r[0], r[6], r[3], r[22]) for r in rows)
    # TASK-014: dedup on the normalised specimen pair after the gbifID-ordered fetch, before
    # any counting; rows without a usable pair (either side empty) each count.
    recs, seen_pair, xdupes = [], set(), 0
    for r in rows:
        if not gate.is_public(species=r[1], species_key=r[0], genus=r[6], taxon_rank=r[3],
                              col_key=r[22]): continue
        pair = specimen_key(r[14], r[15])
        if pair is not None:
            if pair in seen_pair:
                xdupes += 1
                continue
            seen_pair.add(pair)
        recs.append(r)
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
    area_evidence, area_depth, area_decades = defaultdict(Counter), defaultdict(Counter), defaultdict(Counter)
    yearless = 0                        # TASK-021: assigned records no decade bin takes
    fam_class = defaultdict(Counter)
    cell_area, tile_prefs = {}, defaultdict(Counter)
    for r in recs:
        (key, sp, fam, rank, bor, cls, gen, lat, lon, mon, yr, loc, prov, dsk, inst, cat,
         unc, prec, iss, genz, held, spk, colk) = r
        fam_class[fam or ""][cls] += 1
        kd = kind(cls, fam, gen or (sp or "").split(" ")[0])
        cell = mesh_cell(lat, lon)
        if cell is not None and spatially_eligible(unc, prec, iss, genz, held):
            aid = island_by_center(*cell) or tile_id(*cell)
            cell_area[cell_key(*cell)] = aid
            if aid.startswith("t"):
                p = norm_pref(prov)
                if p: tile_prefs[aid][p] += 1
        else:
            # §6/§7 (TASK-005): without a usable public-resolution position (missing, rejected
            # or ineligible), a record never reaches a spatial output. It may still count in an
            # island's record totals by name — by_name reports that as 0 (§1C keeps the column).
            cell, aid = None, island_by_name(loc, prov)
            if not aid: continue
        area_recs[aid].append((key, sp, fam, rank, EVID.get(bor, "o"), cell, mon, yr, kd, dsk, spk, colk))
        # TASK-021: the Overview aggregates count exactly the records totals.records counts
        # (the gated, area-assigned set just built — spatial and name-routed alike).
        area_evidence[aid][bor] += 1
        area_depth[aid][id_depth_bucket(rank)] += 1
        if yr is not None and 1850 <= yr <= 2100:      # the national decades window
            area_decades[aid][yr // 10 * 10] += 1
        else:
            yearless += 1
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

    def public_center(bbox):
        """§6/§1B candidate anchor: the mean of the public JIS cell centres whose centres lie
        inside the area's index bbox — deterministic and independent of every record (a box
        too small to hold a cell centre falls back to the bbox centre). Candidate distances
        are computed only from this and from snapped cell centroids, never raw coordinates."""
        south, north, west, east = bbox
        rl = list(range(max(ROW_MIN, math.ceil(south * MESH_LAT_CELLS - 0.5 - MESH_EPS)),
                        min(ROW_MAX, math.floor(north * MESH_LAT_CELLS - 0.5 + MESH_EPS)) + 1))
        cl = list(range(max(COL_MIN, math.ceil(west * MESH_LON_CELLS - 0.5 - MESH_EPS)),
                        min(COL_MAX, math.floor(east * MESH_LON_CELLS - 0.5 + MESH_EPS)) + 1))
        if not rl or not cl: return ((south + north) / 2, (west + east) / 2)
        return (sum(cell_center(r, cl[0])[0] for r in rl) / len(rl),
                sum(cell_center(rl[0], cc)[1] for cc in cl) / len(cl))

    jp_points = defaultdict(lambda: defaultdict(lambda: [0] * 13))   # species -> (cell centre, label) -> [12 months + total]
    for aid, lst in area_recs.items():
        for (key, sp, fam, rank, ev, cell, mon, yr, kd, dsk, spk, colk) in lst:
            if rank in SPECIES_RANKS and sp and cell is not None:   # eligible records only (TASK-005)
                # TASK-018: identity is the COL key — backbone synonyms lumped under one
                # accepted id share the tuple, so they merge as one candidate source.
                v = jp_points[(spk, fam or "", kd, sp, colk)][(*cell_center(*cell), aid)]
                v[12] += 1
                # TASK-012: candidate months are fruiting evidence; the v[12] record total keeps all
                if ev != "d" and mon and 1 <= mon <= 12: v[mon - 1] += 1
    if "nb" in tables:
        nb_cols = {r[0] for r in c.execute(
            "select column_name from information_schema.columns where table_name='nb'").fetchall()}
        N = exprs("nb", nb_cols)
        for fam, cls, n in c.execute(f"""select {N['family']}, {N['class']}, count(*) from nb
                                         where {N['family']} is not null group by 1,2""").fetchall():
            fam_class[fam][cls] += n
    def fam_kind(fam, sp):
        # nb rows can carry a null class (publish run 45192355587); None must not
        # reach the sort's tiebreak — empty string sorts last, keeps ties deterministic
        cls = sorted(fam_class[fam].items(),
                     key=lambda kv: (-kv[1], kv[0] is None, kv[0] or ""))[0][0] if fam_class.get(fam) else None
        return kind(cls, fam, (sp or "").split(" ")[0])

    if "ea" in tables:
        ea_cols = {r[0] for r in c.execute(
            "select column_name from information_schema.columns where table_name='ea'").fetchall()}
        E = exprs("ea", ea_cols)
        for key, sp, colk, spk, fam, lat, lon, mon, n in c.execute(
                f"""select speciesKey, {E['sp_name']}, {E['col_key']}, {E['sp_key']}, {E['family']},
                           try_cast(lat as double), try_cast(lon as double), try_cast("month" as int),
                           try_cast(n as int) from ea where species is not null""").fetchall():
            if not gate.is_public(species=sp, species_key=key, col_key=colk) or lat is None: continue
            v = jp_points[(spk, fam or "", fam_kind(fam, sp), sp, colk)][(lat, lon, "ea")]
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
        for (spk, fam, kd, sp, colk), pts in jp_points.items():
            if spk in present: continue
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
            if score > 0 and (spk not in best or score > best[spk][0]): best[spk] = (score, fam, near, months, kd, sp, colk)
        out = []
        for k in ("m", "l", "o"):            # top 40 of each kind, scored against the best of that kind
            top = sorted(((spk, v) for spk, v in best.items() if v[4] == k),
                         key=lambda kv: (-kv[1][0], kv[0]))[:40]
            if not top: continue
            mx = top[0][1][0]
            # nearby-evidence lead: d is the lower edge of a 50 km band from the public area
            # centre to the nearest source centroid (§6); s stays the 1–100 within-kind priority.
            out += [{"n": sp, "f": fam, "k": kd, "s": max(1, round(100 * sc / mx)),
                     "d": min(BAND_CAP, BAND_KM * int(near[0] // BAND_KM)), "w": near[1][0],
                     "wj": near[1][1], "m": months, "c": colk or None,
                     # TASK-021: with no COL key, spk IS 'L' + speciesKey (col_xr sp_key),
                     # so the legacy fallback key is already carried — strip the marker
                     "lk": spk[1:] if not colk and isinstance(spk, str) and spk.startswith("L") else None}
                    for spk, (sc, fam, near, months, kd, sp, colk) in top]
        return out

    # ---------- write area files ---------------------------------------------------------------
    def overview(ev, dep, dec):
        """TASK-021: the three Overview aggregates in their published shape — evidence
        by n desc then basis (null last, as the national SQL orders), id_depth the fixed
        species/genus/higher triple, decades ascending."""
        return {"evidence": sorted(ev.items(), key=lambda kv: (-kv[1], kv[0] is None, kv[0] or "")),
                "id_depth": [(b, dep.get(b, 0)) for b in ("species", "genus", "higher")],
                "decades": sorted(dec.items())}

    def merged_counts(members, field):
        """Group aggregate: sum the members' published arrays, as totals does."""
        cnt = Counter()
        for m in members:
            for k, n in built[m][field]: cnt[k] += n
        return cnt

    os.makedirs(a.out, exist_ok=True)
    index, built = [], {}
    for aid, lst in sorted(area_recs.items()):
        isl = next((x for x in islands if x["id"] == aid), None)
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
        center = public_center(bbox)
        by_sp = {}
        unresolved = 0
        months_all = [0] * 12
        for (key, sp, fam, rank, ev, cell, mon, yr, kd, dsk, spk, colk) in lst:
            # TASK-012: months are fruiting evidence (non-'d'); totals/sq/y/e keep every record
            if ev != "d" and cell is not None and mon and 1 <= mon <= 12: months_all[mon - 1] += 1
            if rank not in SPECIES_RANKS or not sp:
                unresolved += 1
                continue
            # TASK-018: keyed by the COL identity — lumped backbone synonyms share spk and
            # merge here; n is the COL accepted name, c the COL key (null when unmapped).
            # TASK-021: lk is the legacy speciesKey, set only when c is null.
            s = by_sp.setdefault(spk, {"n": sp, "c": colk or None,
                                       "lk": str(key) if not colk and key is not None else None,
                                       "f": fam or "", "k": kd, "r": 0,
                                       "m": [0] * 12, "sq": set(), "y": [9999, 0], "e": Counter()})
            s["r"] += 1
            if cell is not None:
                if ev != "d" and mon and 1 <= mon <= 12: s["m"][mon - 1] += 1
                s["sq"].add(cell_key(*cell))
            if yr: s["y"] = [min(s["y"][0], yr), max(s["y"][1], yr)]
            s["e"][ev] += 1
        species = []
        for s in sorted(by_sp.values(), key=lambda s: (-s["r"], s["n"])):
            # §6 RecordedRow (TASK-005): habitat prose (h), localities (l), elevation (el) and the
            # by-name count (nm) are withdrawn — empty values; the site renders the em dash.
            # n/f/k/r/m/sq/y/e keep their meaning. sq/m come from eligible whole cells only.
            # c is the additive COL XR key (TASK-018); lk the additive legacy key (TASK-021).
            species.append({"n": s["n"], "c": s["c"], "lk": s["lk"], "f": s["f"], "k": s["k"], "r": s["r"], "m": s["m"],
                            "sq": len(s["sq"]), "y": s["y"] if s["y"][1] else None,
                            "e": dict(sorted(s["e"].items(), key=lambda kv: (-kv[1], kv[0]))),
                            "h": [], "l": [], "nm": 0, "el": None})
        out = {"id": aid, "kind": "island" if isl else "tile", "name_en": name_en, "name_ja": name_ja,
               "group_en": group_en, "group_ja": group_ja, "bbox": [round(x, 4) for x in bbox],
               "schema_version": 1, "snapshot": "",
               "totals": {"records": len(lst), "by_name": 0, "species": len(species), "unresolved": unresolved,
                          "squares": len({cell_key(*r[5]) for r in lst if r[5] is not None})},
               "months": months_all,
               **overview(area_evidence[aid], area_depth[aid], area_decades[aid]),
               "top_localities": [],
               # §6: a zero-record entry keeps its route and public reference bbox with empty data
               "species": species, "possible": possible_for(aid, center, set(by_sp)) if lst else []}
        # TASK-012: non-locational sampling bias note — the top datasetKey's share of area records
        dk = Counter(r[9] for r in lst if r[9])
        if dk:
            top_key, top_n = sorted(dk.items(), key=lambda kv: (-kv[1], kv[0]))[0]   # deterministic under shuffles
            if top_n / len(lst) >= 0.30:
                out["dominant_source"] = [dataset_title(top_key), round(top_n / len(lst), 2)]
        write_json(os.path.join(a.out, f"{aid}.json"), out)
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
                # complete cell unions (§6): group rows merge the members' whole-cell sq and
                # eligible months; withdrawn fields (h/l/el/nm) stay empty placeholders.
                # PR #67 review: rows merge by name AND taxon identity — homonymous taxa
                # (same name, different key) stay separate rows so each keeps its keys,
                # its details linkage and its own distribution, instead of one keyless
                # merged row that name-matches the wrong entry client-side.
                gkey = (x["n"], x.get("c"), x.get("lk"))
                t = sp.setdefault(gkey, {"n": x["n"], "c": x["c"], "lk": x["lk"], "f": x["f"],
                                        "k": x["k"], "r": 0, "m": [0] * 12, "sq": 0, "y": None,
                                        "e": Counter(), "h": [], "l": [], "nm": 0, "i": [], "el": None})
                t["r"] += x["r"]; t["sq"] += x["sq"]; t["i"].append(mid)
                t["m"] = [p + q for p, q in zip(t["m"], x["m"])]
                if x["y"]: t["y"] = [min(t["y"][0], x["y"][0]), max(t["y"][1], x["y"][1])] if t["y"] else list(x["y"])
                t["e"].update(x["e"])
        species = sorted(({**v, "e": dict(v["e"])} for v in sp.values()), key=lambda v: (-v["r"], v["n"]))
        poss = {}
        # PR #12 review: candidate exclusion on the same identity tuple as the species
        # map (a name-only check against tuple keys never matched, so a taxon recorded
        # on one member could still surface as a group candidate) — plus the name, so a
        # differently-keyed homonym of a recorded taxon stays a candidate only if its
        # own identity was never recorded on any member
        recorded_names = {k[0] for k in sp}
        for mid in members:
            for x in built[mid]["possible"]:
                ident = (x["n"], x.get("c"), x.get("lk"))
                if ident in sp or x["n"] in recorded_names: continue
                if x["n"] not in poss or x["s"] > poss[x["n"]]["s"]: poss[x["n"]] = {**x, "i": mid}
        possible = sorted(poss.values(), key=lambda v: (v["k"], -v["s"], v["n"]))
        bb = [min(built[m]["bbox"][0] for m in members), max(built[m]["bbox"][1] for m in members),
              min(built[m]["bbox"][2] for m in members), max(built[m]["bbox"][3] for m in members)]
        tot = {k: sum(built[m]["totals"][k] for m in members) for k in ("records", "by_name", "unresolved", "squares")}
        tot["species"] = len(species)
        out = {"id": gid, "kind": "group", "members": members, "name_en": gen, "name_ja": gja, "group_en": gen, "group_ja": gja,
               "bbox": bb, "schema_version": 1, "snapshot": "", "totals": tot,
               "months": [sum(built[m]["months"][k] for m in members) for k in range(12)],
               **overview(merged_counts(members, "evidence"), merged_counts(members, "id_depth"),
                          merged_counts(members, "decades")),
               "top_localities": [], "species": species, "possible": possible,
               "member_names": {m: [built[m]["name_en"], built[m]["name_ja"]] for m in members}}
        write_json(os.path.join(a.out, f"{gid}.json"), out)
        index.append({"id": gid, "kind": "group", "name_en": gen, "name_ja": gja, "group_en": gen, "group_ja": gja,
                      "bbox": bb, "records": tot["records"], "species": len(species), "members": members})

    # §6 v1 AreaIndex shape; snapshot stays empty until pipeline/snapshot.py stamps the bundle.
    write_json(os.path.join(a.out, "index.json"),
               {"schema_version": 1, "snapshot": "", "areas": index, "cells": dict(sorted(cell_area.items())),
                "masked_records": masked, "has_habitat": False, "has_neighbours": "ea" in tables})
    print(f"{len(index)} areas ({sum(1 for x in index if x['kind']=='island')} islands), {len(cell_area)} squares mapped, "
          f"{masked} sensitive records excluded, precision flags applied={sum(x != 'null' for x in sel)}/5, "
          f"neighbours={'ea' in tables}, dominant_source on {sum(1 for x in built.values() if 'dominant_source' in x)} area(s), "
          f"cross-dataset duplicates removed: {xdupes}, "
          f"records left out of decades (no usable year): {yearless}")
    print(f"gate audit (aggregate): {audit}")


if __name__ == "__main__":
    main()
