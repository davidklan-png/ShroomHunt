#!/usr/bin/env python3
"""The definition of publishable (plan §6/§7, TASK-006): validate a staging
directory before anything is cloned or copied to Bio_HP.

Usage: python3 pipeline/validate_dashboard_publish.py <staging-dir> \
         [--mask ...] [--coverage ...] [--exceptions ...] [--redlist ...] [--col-mask-map ...]

Exit 0 = publishable. Exit 1 = NOT publishable, with every reason on stderr.

A staging directory holding dashboard.json is the occurrence bundle
(dashboard.json, areas/index.json, areas/<id>.json for exactly the index's
ids, optional japan-outline.json and mesh-research.json, and — TASK-024,
spec item 7 — species/index.json plus the species/<genus-slug>.json files
its files map names). Without dashboard.json, it is the geography-only
bundle: exactly japan-outline.json. Anything else — dotfiles, scripts,
stray JSON — rejects.

Checks:
  allowlist   exactly the files the builders own (above);
  shape       every file parses, carries schema_version 1 (unknown/missing
              rejects, naming the asset) and matches its §6 v1 shape: key
              sets, types, enums (Kind/AreaKind), Bins12 length, Box arity.
              Island/tile area files may carry an optional dominant_source
              [dataset title, share] pair (share 0.30-1.0, TASK-012); a group
              file never does (groups merge members, the builder emits it
              per sampling area only);
              every area file (island, tile or group) may carry the three
              additive Overview aggregates, as an all-or-nothing trio uniform
              across the bundle: evidence [[basisOfRecord|null, n]] over the
              area's public records, id_depth the fixed [species, genus,
              higher] bucket triple, decades [[decade, n]] with year-less
              records left out — evidence and id_depth must each sum to
              totals.records, decades at most; area species/candidate rows may
              also carry a nullable "lk" and leads a 7th legacy-key element —
              never non-null on a row whose "c"/col_key is set (a COL-keyed row
              fires the primary /taxon/ link and needs no fallback);
  snapshot    a 64-hex lowercase digest, identical in every occurrence file
              and equal to the recomputed bundle digest (pipeline/snapshot.py);
  integrity   dashboard mesh key set == index cells keys; index area ids ==
              detail file ids == groups' members; cells values are island/tile
              ids present in the index; outline places[].area_id (when set)
              exists in the index;
  gate        public_taxa.load_gate() is rerun against the reference inputs,
              col_mask_map included (TASK-018: it is a required input here, so
              CI cannot publish without it); no masked/quarantined name and no
              masked COL key or masked legacy key (c/lk, TASK-021) may appear
              in any species/candidate/lead list, and no masked key, legacy key
              or name anywhere under species/ (TASK-024) — offenders are named
              by position, never by taxon — protected names stay out of logs);
              species/index.json must cover exactly the genus files on disk
              (counts, bytes, sha256), carry the recomputed species digest as
              its snapshot, and every entry must match the item-7 shape, with a
              photo only when author AND licence AND licence_url AND file_page
              are all present (else null) and area ids referencing non-group
              index areas;
  prose       no habitat/locality/elevation prose: withdrawn fields must be
              empty and no string may match the known prose fixture patterns;
  budget      gzip-9 size caps per file (§6 table);
  rectangle   every published mesh centre reconstructs to its key's rectangle
              (full set: cheap enough, and it is the publication geometry).
"""
import argparse, gzip, hashlib, json, os, re, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from geo_ja import (COL_MAX, COL_MIN, ROW_MAX, ROW_MIN, cell_center,  # noqa: E402
                    mesh_cell)
from public_taxa import load_gate  # noqa: E402
from snapshot import bundle_digest  # noqa: E402

KIB = 1024
BUDGETS = {"dashboard.json": 22, "areas/index.json": 25, "areas/okinawa-honto.json": 24,
           "japan-outline.json": 36, "mesh-research.json": 60,
           # TASK-024: one sha256/bytes/species entry per genus (~3k genera on the
           # 2026-11 data); the hex digests do not compress — measured 152 KiB gzip-9
           "species/index.json": 160}                                 # gzip-9 KiB caps (§6)
DETAIL_BUDGET = 64
KINDS = {"m", "l", "o"}
AREA_KINDS = {"island", "tile", "group"}
RANKS = {"species", "genus", "higher"}
AGG_KEYS = {"evidence", "id_depth", "decades"}   # TASK-021: the Overview aggregate trio
TIERS = {"s", "d", "o"}                          # TASK-024: records_by_tier evidence codes
SPECIES_LEVELS = {"species", "genus", None}      # MushroomExpert link level, nullable
DECADE_MIN, DECADE_MAX = 1850, 2100              # the national decades window
BANDS = {0, 50, 100, 150, 200, 250}
HEX64 = re.compile(r"^[0-9a-f]{64}$")
DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MESH_KEY = re.compile(r"^(\d+):(\d+)$")
# Known prose fixture patterns (the TASK-005 sanitization fixtures plus generic
# habitat prepositions and obscuring notes): substring scan over every string
# in every file. Structural emptiness of the withdrawn fields is checked
# separately, so this only has to catch prose smuggled into surviving fields.
PROSE_PATTERNS = ("hidden corner meadow", "decaying pinus", "castanopsis sieboldii",
                  "location obscured", "coordinates rounded", "withheld for conservation",
                  "on dead ", "on decaying ", "growing on ")

DASH_KEYS = {"schema_version", "snapshot", "generated", "totals", "evidence", "id_depth", "phyla",
             "decades", "families", "mesh", "leads", "lead_totals", "unresolved_genera",
             "mushroom_observer_japan", "sources"}
TOTAL_KEYS = {"records", "species", "genera", "families", "datasets", "georeferenced", "to_species",
              "sensitive_species", "sensitive_records", "redlist_names"}
INDEX_KEYS = {"schema_version", "snapshot", "areas", "cells", "masked_records", "has_habitat",
              "has_neighbours"}
AREA_BASE = {"id", "kind", "name_en", "name_ja", "group_en", "group_ja", "bbox"}
INDEX_ROW = AREA_BASE | {"records", "species"}
AREA_FILE = AREA_BASE | {"schema_version", "snapshot", "totals", "months", "top_localities",
                         "species", "possible"}
GROUP_FILE = AREA_FILE | {"members", "member_names"}
RECORDED = {"n", "c", "f", "k", "r", "m", "sq", "y", "e", "h", "l", "nm", "el"}
CANDIDATE = {"n", "c", "f", "k", "s", "d", "w", "wj", "m"}
OUTLINE_KEYS = {"schema_version", "source", "bbox", "land", "places"}
RESEARCH_KEYS = {"schema_version", "snapshot", "slices", "genera"}
# TASK-024 (spec item 7): the species bundle — per-genus files plus the index.
SPECIES_INDEX_KEYS = {"schema_version", "snapshot", "generated", "genera", "species", "files"}
SPECIES_FILE_KEYS = {"schema_version", "genus", "species"}
SPECIES_ENTRY_KEYS = {"key", "legacy_key", "name", "name_ja", "family", "order", "class", "kind",
                      "records_by_tier", "fruiting_months", "area_ids", "elevation_band",
                      "host_genera", "east_asia", "could_be_found_area_ids", "first_decade",
                      "last_decade", "external"}
EXTERNAL_KEYS = {"wikidata_qid", "wikipedia_ja", "wikipedia_en", "photo", "mhlw_url",
                 "endemic_hosoya2016", "literature", "mushroomexpert_url",
                 "mushroomexpert_level"}
PHOTO_KEYS = {"thumb_url", "file_page", "author", "licence", "licence_url"}
GENUS_SLUG = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


# ---------- small typed predicates (bool is not a number, True is not a UInt) ----------------

def is_uint(v):
    return isinstance(v, int) and not isinstance(v, bool) and v >= 0


def is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def is_bins12(v):
    return isinstance(v, list) and len(v) == 12 and all(is_uint(x) for x in v)


def is_box(v):
    return (isinstance(v, list) and len(v) == 4 and all(is_num(x) for x in v)
            and v[0] <= v[1] and v[2] <= v[3])          # [south, north, west, east]


def is_mesh_key(k):
    m = MESH_KEY.match(k or "")
    return bool(m) and ROW_MIN <= int(m.group(1)) < ROW_MAX and COL_MIN <= int(m.group(2)) < COL_MAX


# ---------- per-asset §6 shape checks ---------------------------------------------------------

def check_dashboard(d, path, reasons):
    keys = f"shape: {path}"
    if set(d) != DASH_KEYS:
        reasons.append(f"{keys}: key set != §6 Dashboard ({sorted(set(d) ^ DASH_KEYS)} differ)")
        return
    listy = ("evidence", "id_depth", "phyla", "decades", "families", "mesh", "leads",
             "unresolved_genera", "sources")
    if not isinstance(d["totals"], dict) or not isinstance(d["lead_totals"], dict) \
            or not all(isinstance(d[k], list) for k in listy):
        reasons.append(f"{keys}: a §6 list/object field has the wrong type")
        return
    if not DAY.match(d["generated"] or ""):
        reasons.append(f"{keys}: generated {d['generated']!r} is not a YYYY-MM-DD day")
    if set(d["totals"]) != TOTAL_KEYS or not all(is_uint(v) for v in d["totals"].values()):
        reasons.append(f"{keys}: totals is not the ten §6 UInt fields")
    for field, arity in (("evidence", 2), ("id_depth", 2), ("phyla", 3), ("decades", 2), ("families", 3)):
        for i, row in enumerate(d[field]):
            if not (isinstance(row, list) and len(row) == arity and all(is_uint(x) for x in row[1:])):
                reasons.append(f"{keys}: {field}[{i}] is not a §6 UInt row of arity {arity}")
            elif field == "id_depth" and row[0] not in RANKS:
                reasons.append(f"{keys}: id_depth[{i}] rank {row[0]!r} not in {sorted(RANKS)}")
            elif field == "evidence" and row[0] is not None and not isinstance(row[0], str):
                reasons.append(f"{keys}: evidence[{i}] basis is neither string nor null")
            elif field in ("phyla", "families") and not isinstance(row[0], str):
                reasons.append(f"{keys}: {field}[{i}] name is not a string")
    for i, row in enumerate(d["mesh"]):
        if not (isinstance(row, list) and len(row) == 4 and is_num(row[0]) and is_num(row[1])
                and is_uint(row[2]) and is_uint(row[3])):
            reasons.append(f"{keys}: mesh[{i}] is not a §6 MeshRow [center_lat, center_lon, records, species]")
    for i, row in enumerate(d["leads"]):
        if not (isinstance(row, list) and len(row) in (6, 7) and isinstance(row[0], str)
                and (row[1] is None or isinstance(row[1], str)) and is_uint(row[2]) and is_uint(row[3])
                and isinstance(row[4], str) and (row[5] is None or isinstance(row[5], str))
                and (len(row) == 6 or row[6] is None or isinstance(row[6], str))):
            reasons.append(f"{keys}: leads[{i}] is not [species, family|null, neighbour_records, "
                           f"mo_records, triage_hint, col_key|null] with an optional 7th legacy key|null")
        elif len(row) == 7 and row[6] is not None and row[5] is not None:
            reasons.append(f"{keys}: leads[{i}] carries the 7th legacy key alongside col_key")
    if set(d["lead_totals"]) != {"candidates", "with_mo", "with_unresolved_jp_genus"} \
            or not all(is_uint(v) for v in d["lead_totals"].values()):
        reasons.append(f"{keys}: lead_totals is not the three §6 UInt fields")
    for i, row in enumerate(d["unresolved_genera"]):
        if not (isinstance(row, list) and len(row) == 3 and isinstance(row[0], str)
                and (row[1] is None or isinstance(row[1], str)) and is_uint(row[2])):
            reasons.append(f"{keys}: unresolved_genera[{i}] is not [genus, family|null, records]")
    if not is_uint(d["mushroom_observer_japan"]):
        reasons.append(f"{keys}: mushroom_observer_japan is not a UInt")
    for i, s in enumerate(d["sources"]):
        if not (isinstance(s, dict) and set(s) == {"key", "doi", "records", "created"}
                and isinstance(s["key"], str) and isinstance(s["doi"], str)
                and (s["records"] is None or is_uint(s["records"]))
                and (s["created"] is None or isinstance(s["created"], str))):
            reasons.append(f"{keys}: sources[{i}] is not {{key, doi, records: UInt|null, created: string|null}}")


def check_index(ix, path, reasons):
    """Returns {id: kind} for the index rows ({} when the shape is too broken to read)."""
    keys = f"shape: {path}"
    if not isinstance(ix, dict) or set(ix) != INDEX_KEYS:
        reasons.append(f"{keys}: key set != §6 AreaIndex")
        return {}
    if not isinstance(ix["areas"], list):
        reasons.append(f"{keys}: areas is not a list")
        ix["areas"] = []
    ids = {}
    for i, row in enumerate(ix["areas"]):
        what = f"areas[{i}]"
        if not isinstance(row, dict):
            reasons.append(f"{keys}: {what} is not an object")
            continue
        want = INDEX_ROW | ({"members"} if row.get("kind") == "group" else set())
        if set(row) != want:
            reasons.append(f"{keys}: {what} key set != §6 AreaIndexRow ({sorted(set(row) ^ want)} differ)")
            continue
        if row["kind"] not in AREA_KINDS:
            reasons.append(f"{keys}: {what} kind {row['kind']!r} not in {sorted(AREA_KINDS)}")
            continue
        if not (isinstance(row["id"], str) and row["id"]) or not is_box(row["bbox"]) \
                or not all(isinstance(row[k], str) and row[k] for k in AREA_BASE - {"bbox", "kind", "id"}) \
                or not is_uint(row["records"]) or not is_uint(row["species"]):
            reasons.append(f"{keys}: {what} has a mistyped §6 AreaBase/count field")
            continue
        if row["kind"] == "group" and not (isinstance(row["members"], list) and row["members"]
                                           and all(isinstance(m, str) for m in row["members"])):
            reasons.append(f"{keys}: {what} group members must be a non-empty Id list")
            continue
        if row["id"] in ids:
            reasons.append(f"{keys}: duplicate area id {row['id']!r}")
        ids[row["id"]] = row["kind"]
    cells = ix["cells"]
    if not isinstance(cells, dict):
        reasons.append(f"{keys}: cells is not an object")
    else:
        for k, v in cells.items():
            if not is_mesh_key(k):
                reasons.append(f"{keys}: cells key {k!r} is not an in-envelope MeshKey 'row:col'")
            if not isinstance(v, str):
                reasons.append(f"{keys}: cells[{k!r}] value is not an Id")
            elif v not in ids:
                reasons.append(f"integrity: {path}: cells[{k!r}] value is not in the index")
            elif ids[v] == "group":
                reasons.append(f"integrity: {path}: cells[{k!r}] -> group; cells reference island/tile ids only")
    if not is_uint(ix["masked_records"]):
        reasons.append(f"{keys}: masked_records is not a UInt")
    if ix["has_habitat"] is not False:
        reasons.append(f"unsafe field: {path}: has_habitat must be false until a governed habitat field exists")
    if not isinstance(ix["has_neighbours"], bool):
        reasons.append(f"{keys}: has_neighbours is not a boolean")
    return ids


def check_area_file(f, path, reasons, ids):
    keys = f"shape: {path}"
    kind = f.get("kind") if isinstance(f, dict) else None
    want = GROUP_FILE if kind == "group" else (AREA_FILE if kind in AREA_KINDS else None)
    # TASK-021: the Overview aggregates ride along as an additive trio (all three
    # keys or none), beside dominant_source's single optional key.
    if want is None or not want <= set(f) or set(f) - want - {"dominant_source"} not in (set(), AGG_KEYS):
        reasons.append(f"{keys}: key set != §6 AreaFile for kind {kind!r}")
        return
    if not all(isinstance(f[k], str) and f[k] for k in ("id", "name_en", "name_ja")) \
            or not all(isinstance(f[k], str) for k in ("group_en", "group_ja")) \
            or not is_box(f["bbox"]) or f["id"] != os.path.basename(path)[:-5]:
        reasons.append(f"{keys}: mistyped §6 AreaBase, or id != filename stem")
        return
    if kind != ids.get(f["id"]):
        reasons.append(f"integrity: {path}: kind {kind!r} != index kind {ids.get(f['id'])!r}")
    if kind == "group" and "dominant_source" in f:
        reasons.append(f"{keys}: dominant_source is an island/tile sampling note; "
                       f"a group file never carries one")
        return
    if not isinstance(f["species"], list) or not isinstance(f["possible"], list) \
            or not isinstance(f["totals"], dict) or (kind == "group" and not isinstance(f["members"], list)) \
            or not all(isinstance(f[k], list) for k in AGG_KEYS & set(f)):
        reasons.append(f"{keys}: a §6 list/object field has the wrong type")
        return
    if kind == "group":
        if not f["members"] or [m for m in f["members"] if m not in ids] != []:
            reasons.append(f"integrity: {path}: members include ids missing from the index")
        if not isinstance(f["member_names"], dict) or set(f["member_names"]) != set(f["members"]) \
                or not all(isinstance(v, list) and len(v) == 2
                           and all(isinstance(x, str) for x in v) for v in f["member_names"].values()):
            reasons.append(f"{keys}: member_names is not {{Id: [name_en, name_ja]}} over members")
    t = f["totals"]
    if not (isinstance(t, dict) and set(t) == {"records", "by_name", "species", "unresolved", "squares"}
            and all(is_uint(t[k]) for k in t)):
        reasons.append(f"{keys}: totals is not the five §6 UInt fields")
    elif t["by_name"] != 0:
        reasons.append(f"unsafe field: {path}: totals.by_name must be 0 (name-only counts are withdrawn)")
    if AGG_KEYS <= set(f):                              # TASK-021: the Overview aggregates —
        n0 = len(reasons)                               # shapes first, then the record-sum ties
        for i, row in enumerate(f["evidence"]):
            if not (isinstance(row, list) and len(row) == 2
                    and (row[0] is None or isinstance(row[0], str)) and is_uint(row[1])):
                reasons.append(f"{keys}: evidence[{i}] is not a [basisOfRecord|null, UInt] pair")
        for i, row in enumerate(f["id_depth"]):
            if not (isinstance(row, list) and len(row) == 2 and row[0] in RANKS and is_uint(row[1])):
                reasons.append(f"{keys}: id_depth[{i}] is not a [rank, UInt] pair over {sorted(RANKS)}")
        # PR #5 review: exactly the species/genus/higher triple, in order — duplicate or
        # missing buckets that still sum right must not publish
        if len(reasons) == n0 and [r[0] for r in f["id_depth"]] != ["species", "genus", "higher"]:
            reasons.append(f"{keys}: id_depth must be exactly the species/genus/higher triple, in order")
        for i, row in enumerate(f["decades"]):
            if not (isinstance(row, list) and len(row) == 2 and is_uint(row[0]) and is_uint(row[1])):
                reasons.append(f"{keys}: decades[{i}] is not a [decade, UInt] pair")
        if len(reasons) == n0 and isinstance(t, dict) and is_uint(t.get("records")):
            # evidence and id_depth partition the same record set totals.records counts;
            # decades misses at most the year-less records
            for field in ("evidence", "id_depth"):
                if sum(r[1] for r in f[field]) != t["records"]:
                    reasons.append(f"integrity: {path}: {field} does not sum to totals.records "
                                   f"({t['records']})")
            if sum(r[1] for r in f["decades"]) > t["records"]:
                reasons.append(f"integrity: {path}: decades sums above totals.records ({t['records']})")
    if not is_bins12(f["months"]):
        reasons.append(f"{keys}: months is not a Bins12")
    if "dominant_source" in f:                              # TASK-012: optional sampling-bias note
        ds = f["dominant_source"]
        if not (isinstance(ds, list) and len(ds) == 2 and isinstance(ds[0], str) and ds[0]
                and is_num(ds[1]) and 0.30 <= ds[1] <= 1.0):
            reasons.append(f"{keys}: dominant_source is not a [dataset title, share] pair "
                           f"with 0.30 <= share <= 1.0")
    if f["top_localities"] != []:
        reasons.append(f"unsafe field: {path}: top_localities must be [] (occurrence-derived "
                       f"localities are withdrawn)")
    rwant = RECORDED | ({"i"} if kind == "group" else set())
    for i, s in enumerate(f["species"]):
        if not isinstance(s, dict) or not rwant <= set(s) <= rwant | {"lk"}:
            reasons.append(f"{keys}: species[{i}] key set != §6 RecordedRow (+ optional lk)")
            continue
        if not (isinstance(s["n"], str) and s["n"] and isinstance(s["f"], str) and s["k"] in KINDS
                and (s["c"] is None or isinstance(s["c"], str))      # TASK-018 COL key, nullable
                and (s.get("lk") is None or isinstance(s["lk"], str))  # TASK-021 legacy key, nullable
                and is_uint(s["r"]) and is_bins12(s["m"]) and is_uint(s["sq"])
                and (s["y"] is None or (isinstance(s["y"], list) and len(s["y"]) == 2
                                        and all(is_uint(x) for x in s["y"]) and s["y"][0] <= s["y"][1]))
                and isinstance(s["e"], dict) and set(s["e"]) <= {"s", "d", "o"}
                and all(is_uint(v) for v in s["e"].values())
                and (kind != "group" or (isinstance(s["i"], list) and s["i"]
                                         and all(m in f["members"] for m in s["i"])))):
            reasons.append(f"{keys}: species[{i}] has a mistyped §6 RecordedRow field")
        if s.get("lk") is not None and s["c"] is not None:
            reasons.append(f"{keys}: species[{i}] carries lk with c set (a COL-keyed row "
                           f"fires the primary link and needs no legacy key)")
        if s["h"] != [] or s["l"] != [] or s["nm"] != 0 or s["el"] is not None:
            reasons.append(f"unsafe field: {path}: species[{i}] carries withdrawn "
                           f"habitat/locality/elevation data")
    cwant = CANDIDATE | ({"i"} if kind == "group" else set())
    for i, p in enumerate(f["possible"]):
        if not isinstance(p, dict) or not cwant <= set(p) <= cwant | {"lk"}:
            reasons.append(f"{keys}: possible[{i}] key set != §6 CandidateRow (+ optional lk)")
            continue
        if not (isinstance(p["n"], str) and p["n"] and isinstance(p["f"], str) and p["k"] in KINDS
                and (p["c"] is None or isinstance(p["c"], str))      # TASK-018 COL key, nullable
                and (p.get("lk") is None or isinstance(p["lk"], str))  # TASK-021 legacy key, nullable
                and is_uint(p["s"]) and 1 <= p["s"] <= 100 and p["d"] in BANDS
                and isinstance(p["w"], str) and isinstance(p["wj"], str) and is_bins12(p["m"])
                and (kind != "group" or p["i"] in f["members"])):
            reasons.append(f"{keys}: possible[{i}] has a mistyped §6 CandidateRow field")
        if p.get("lk") is not None and p["c"] is not None:
            reasons.append(f"{keys}: possible[{i}] carries lk with c set")


def check_outline(o, path, reasons):
    keys = f"shape: {path}"
    if not isinstance(o, dict) or set(o) != OUTLINE_KEYS:
        reasons.append(f"{keys}: key set != §6 MapReference ({sorted(set(o or {}) ^ OUTLINE_KEYS)} differ)")
        return
    if o["schema_version"] != 1:
        reasons.append(f"schema_version: {path}: {o['schema_version']!r} is not 1")
    if not (isinstance(o["source"], dict) and set(o["source"]) == {"name", "release", "url"}
            and o["source"]["name"] == "Natural Earth"
            and all(isinstance(o["source"][k], str) for k in ("release", "url"))):
        reasons.append(f"{keys}: source is not the pinned Natural Earth provenance")
    if not is_box(o["bbox"]):
        reasons.append(f"{keys}: bbox is not a §6 Box [south, north, west, east]")
    if not isinstance(o["land"], list):
        o["land"] = []
        reasons.append(f"{keys}: land is not a list")
    if not isinstance(o["places"], list):
        o["places"] = []
        reasons.append(f"{keys}: places is not a list")
    for i, poly in enumerate(o["land"]):
        if not isinstance(poly, list) or not poly:
            reasons.append(f"{keys}: land[{i}] is not a polygon (ring list)")
            continue
        for j, ring in enumerate(poly):
            if not (isinstance(ring, list) and len(ring) >= 4
                    and all(isinstance(pt, list) and len(pt) == 2 and all(is_num(x) for x in pt)
                            for pt in ring)):
                reasons.append(f"{keys}: land[{i}][{j}] is not a ring of [lon, lat] points")
            elif ring[0] != ring[-1]:
                reasons.append(f"{keys}: land[{i}][{j}] ring is not closed")
    for i, p in enumerate(o["places"]):
        if not isinstance(p, dict) or not {"en", "ja", "aliases", "at"} <= set(p) \
                or not set(p) <= {"en", "ja", "aliases", "at", "area_id"}:
            reasons.append(f"{keys}: places[{i}] key set != §6 places entry")
            continue
        if not (isinstance(p["en"], str) and p["en"] and isinstance(p["ja"], str)
                and isinstance(p["aliases"], list) and all(isinstance(x, str) for x in p["aliases"])
                and isinstance(p["at"], list) and len(p["at"]) == 2 and all(is_num(x) for x in p["at"])
                and ("area_id" not in p or isinstance(p["area_id"], str))):
            reasons.append(f"{keys}: places[{i}] has a mistyped field")


def check_research(r, path, reasons, ids):
    keys = f"shape: {path}"
    if not isinstance(r, dict) or set(r) != RESEARCH_KEYS:
        reasons.append(f"{keys}: key set != §6 Research")
        return
    if not isinstance(r["slices"], list) or not isinstance(r["genera"], list):
        reasons.append(f"{keys}: a §6 list field has the wrong type")
        return
    for i, row in enumerate(r["slices"]):
        if not (isinstance(row, list) and len(row) == 3 and is_uint(row[0])
                and (row[1] == "all" or row[1] in KINDS) and isinstance(row[2], list)):
            reasons.append(f"{keys}: slices[{i}] is not [month, kind, cells]")
            continue
        for j, cell in enumerate(row[2]):
            if not (isinstance(cell, list) and len(cell) == 3 and is_mesh_key(cell[0])
                    and is_uint(cell[1]) and cell[1] <= 4
                    and (cell[2] is None or (is_uint(cell[2]) and cell[2] <= 4))):
                reasons.append(f"{keys}: slices[{i}].cells[{j}] is not "
                               f"[MeshKey, count_band, genus_share_band|null]")
    for i, row in enumerate(r["genera"]):
        # TASK-008: an optional third element carries the common-name strings
        # [label_en, label_ja, *aliases] for Name search — strings only, and
        # every other arity stays rejected.
        if not (isinstance(row, list) and len(row) in (2, 3) and isinstance(row[0], str)
                and isinstance(row[1], list) and all(a in ids for a in row[1])
                and (len(row) == 2 or (isinstance(row[2], list)
                                       and all(isinstance(x, str) for x in row[2])))):
            reasons.append(f"{keys}: genera[{i}] is not [reviewed_genus, areas] with an optional "
                           f"string-names element, over index ids")


# ---------- the species bundle (TASK-024, spec item 7) ----------------------------------------

def _check_species_entry(e, path, i, reasons, ids):
    """One item-7 species entry: exact key set, per-field types, the photo
    completeness rule and area-id references."""
    keys = f"shape: {path}"
    what = f"species[{i}]"
    if not isinstance(e, dict) or set(e) != SPECIES_ENTRY_KEYS:
        reasons.append(f"{keys}: {what} key set != item 7 species entry")
        return
    if not (isinstance(e["name"], str) and e["name"]
            and (e["key"] is None or isinstance(e["key"], str))
            and (e["legacy_key"] is None or isinstance(e["legacy_key"], str))
            and (e["name_ja"] is None or isinstance(e["name_ja"], str))
            and all(isinstance(e[k], str) for k in ("family", "order", "class"))
            and e["kind"] in KINDS
            and isinstance(e["records_by_tier"], dict) and set(e["records_by_tier"]) <= TIERS
            and all(is_uint(v) for v in e["records_by_tier"].values())
            and is_bins12(e["fruiting_months"])
            and isinstance(e["area_ids"], list) and all(isinstance(x, str) for x in e["area_ids"])
            and (e["elevation_band"] is None or (isinstance(e["elevation_band"], list)
                                                 and len(e["elevation_band"]) == 2
                                                 and all(is_num(x) for x in e["elevation_band"])
                                                 and e["elevation_band"][0] <= e["elevation_band"][1]))
            and (e["host_genera"] is None or (isinstance(e["host_genera"], list)
                                              and e["host_genera"]
                                              and all(isinstance(x, str) and x for x in e["host_genera"])))
            and e["east_asia"] in (True, False)
            and isinstance(e["could_be_found_area_ids"], list)
            and all(isinstance(x, str) for x in e["could_be_found_area_ids"])
            and all(e[k] is None or (is_uint(e[k]) and DECADE_MIN <= e[k] <= DECADE_MAX)
                    for k in ("first_decade", "last_decade"))):
        reasons.append(f"{keys}: {what} has a mistyped item-7 field")
    if e.get("legacy_key") is not None and e.get("key") is not None:
        reasons.append(f"{keys}: {what} carries legacy_key with key set (a COL-keyed entry "
                       f"fires the primary link and needs no legacy fallback)")
    if isinstance(e.get("first_decade"), int) and isinstance(e.get("last_decade"), int) \
            and e["first_decade"] > e["last_decade"]:
        reasons.append(f"{keys}: {what} first_decade > last_decade")
    for field in ("area_ids", "could_be_found_area_ids"):
        for j, aid in enumerate(e.get(field) or []):
            if aid not in ids:
                reasons.append(f"integrity: {path}: {what}.{field}[{j}] is not in the area index")
            elif ids[aid] == "group":
                reasons.append(f"integrity: {path}: {what}.{field}[{j}] is a group; island/tile "
                               f"ids only (groups derive from their members)")
    x = e.get("external")
    if not isinstance(x, dict) or set(x) != EXTERNAL_KEYS:
        reasons.append(f"{keys}: {what}.external key set != the item 7 external block")
        return
    if not ((x["wikidata_qid"] is None or isinstance(x["wikidata_qid"], str))
            and (x["wikipedia_ja"] is None or isinstance(x["wikipedia_ja"], str))
            and (x["wikipedia_en"] is None or isinstance(x["wikipedia_en"], str))
            and (x["mhlw_url"] is None or isinstance(x["mhlw_url"], str))
            and x["endemic_hosoya2016"] in (True, False)
            and (x["mushroomexpert_url"] is None or isinstance(x["mushroomexpert_url"], str))
            and x["mushroomexpert_level"] in SPECIES_LEVELS):
        reasons.append(f"{keys}: {what}.external has a mistyped field")
    p = x["photo"]
    if p is not None and not (isinstance(p, dict) and set(p) == PHOTO_KEYS
                              and all(isinstance(p[k], str) and p[k] for k in PHOTO_KEYS)):
        reasons.append(f"{keys}: {what}.external.photo needs thumb_url, file_page, author, "
                       f"licence AND licence_url, all non-empty — or null")
    if not isinstance(x["literature"], list) or len(x["literature"]) > 5 \
            or not all(isinstance(r, dict) and set(r) == {"doi", "title", "year"}
                       and isinstance(r["doi"], str) and r["doi"]
                       and isinstance(r["title"], str) and r["title"]
                       and (r["year"] is None or (is_uint(r["year"])))
                       for r in x["literature"]):
        reasons.append(f"{keys}: {what}.external.literature is not <=5 {{doi, title, year: "
                       f"UInt|null}} rows")


def check_species(files, docs, staging, reasons, ids):
    """species/ acceptance (TASK-024): the index covers exactly the genus files on
    disk with matching hashes and counts, carries the recomputed species digest,
    and every entry matches the item-7 shape (photo completeness included)."""
    ix = docs.get("species/index.json")
    if not isinstance(ix, dict):
        reasons.append("shape: species/index.json is missing or not an object (species/ files "
                       "are present)")
        return
    if set(ix) != SPECIES_INDEX_KEYS:
        reasons.append(f"shape: species/index.json key set != the item 7 index "
                       f"({sorted(set(ix) ^ SPECIES_INDEX_KEYS)} differ)")
        return
    if ix["schema_version"] != 1:
        reasons.append(f"schema_version: species/index.json: {ix['schema_version']!r} is not 1")
    if not DAY.match(ix["generated"] or ""):
        reasons.append(f"shape: species/index.json: generated {ix['generated']!r} is not a "
                       f"YYYY-MM-DD day")
    fmap = ix["files"]
    if not isinstance(fmap, dict) or not fmap \
            or not all(isinstance(v, dict) and set(v) == {"sha256", "species", "bytes"}
                       and is_uint(v["species"]) and is_uint(v["bytes"])
                       and isinstance(v["sha256"], str) for v in fmap.values()):
        reasons.append("shape: species/index.json: files is not {slug: {sha256, species, bytes}}")
        return
    for slug in fmap:
        if not GENUS_SLUG.match(slug or ""):
            reasons.append(f"shape: species/index.json: files key {slug!r} is not a genus slug")
    expected = {f"species/{s}.json" for s in fmap} | {"species/index.json"}
    for rel in files:
        if rel.startswith("species/") and rel not in expected:
            reasons.append(f"stray file: {rel} is not in the species index")
    for slug in fmap:
        if f"species/{slug}.json" not in files:
            reasons.append(f"missing species file: species/{slug}.json is in the index but not "
                           f"on disk")
    if is_uint(ix["genera"]) and ix["genera"] != len(fmap):
        reasons.append(f"integrity: species/index.json: genera {ix['genera']} != {len(fmap)} files")
    if is_uint(ix["species"]) and ix["species"] != sum(v["species"] for v in fmap.values()):
        reasons.append(f"integrity: species/index.json: species {ix['species']} != the summed "
                       f"per-file counts")
    digest = hashlib.sha256()
    for slug in sorted(fmap):
        path = os.path.join(staging, "species", f"{slug}.json")
        if not os.path.exists(path):
            continue                                  # already reported missing above
        with open(path, "rb") as fh:
            raw = fh.read()
        digest.update(raw)
        meta = fmap[slug]
        if meta["sha256"] != hashlib.sha256(raw).hexdigest() or meta["bytes"] != len(raw):
            reasons.append(f"integrity: species/{slug}.json does not match its index entry "
                           f"(sha256/bytes)")
        d = docs.get(f"species/{slug}.json")
        if not isinstance(d, dict) or set(d) != SPECIES_FILE_KEYS:
            reasons.append(f"shape: species/{slug}.json key set != the item 7 genus file")
            continue
        if d["schema_version"] != 1:
            reasons.append(f"schema_version: species/{slug}.json: {d['schema_version']!r} is not 1")
        if not isinstance(d["genus"], str) or re.sub(r"[^a-z0-9]+", "-", d["genus"].lower()).strip("-") != slug:
            reasons.append(f"integrity: species/{slug}.json genus does not slug to its filename")
        if not isinstance(d["species"], list):
            reasons.append(f"shape: species/{slug}.json: species is not a list")
            continue
        if meta["species"] != len(d["species"]):
            reasons.append(f"integrity: species/{slug}.json: index species count "
                           f"{meta['species']} != {len(d['species'])} entries")
        for i, e in enumerate(d["species"]):
            _check_species_entry(e, f"species/{slug}.json", i, reasons, ids)
    if not (isinstance(ix["snapshot"], str) and HEX64.match(ix["snapshot"])):
        reasons.append(f"snapshot: species/index.json: {ix['snapshot']!r} is not a 64-hex "
                       f"lowercase digest")
    elif ix["snapshot"] != digest.hexdigest():
        reasons.append("digest mismatch: species/index.json: stamped snapshot != recomputed "
                       "species digest")


# ---------- the scan pieces -------------------------------------------------------------------

def iter_strings(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, list):
        for v in obj:
            yield from iter_strings(v)
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from iter_strings(v)


def gate_scan(docs, gate, reasons):
    """No masked/quarantined name and no masked COL key in any species/candidate/lead
    list. Listed names are all identified-to-species rows, so they classify with
    taxon_rank SPECIES (genera lists with GENUS), exactly like the builders'
    semantics; "c" col-key fields and the leads' appended col_key classify by key."""

    def listed(name, rank):
        return gate.classify(species=name, taxon_rank="SPECIES") if rank == "SPECIES" \
            else gate.classify(genus=name, taxon_rank="GENUS")

    def col_listed(key):
        return key is not None and str(key) in gate.keys

    for path, doc in docs.items():
        if path == "dashboard.json" and isinstance(doc, dict):
            for i, row in enumerate(doc.get("leads", []) if isinstance(doc.get("leads"), list) else []):
                if isinstance(row, list) and row and listed(row[0], "SPECIES"):
                    reasons.append(f"protected taxon: {path}: leads[{i}] is masked or quarantined by the gate")
                if isinstance(row, list) and len(row) in (6, 7) and col_listed(row[5]):
                    reasons.append(f"protected taxon: {path}: leads[{i}] carries a masked col_key")
                if isinstance(row, list) and len(row) == 7 and col_listed(row[6]):
                    reasons.append(f"protected taxon: {path}: leads[{i}] carries a masked legacy key")
            for i, row in enumerate(doc.get("unresolved_genera", [])
                                    if isinstance(doc.get("unresolved_genera"), list) else []):
                if isinstance(row, list) and row and listed(row[0], "GENUS"):
                    reasons.append(f"protected taxon: {path}: unresolved_genera[{i}] is in a masked genus")
        elif path.startswith("areas/") and isinstance(doc, dict):
            for i, s in enumerate(doc.get("species", []) if isinstance(doc.get("species"), list) else []):
                if isinstance(s, dict) and listed(s.get("n"), "SPECIES"):
                    reasons.append(f"protected taxon: {path}: species[{i}] is masked or quarantined by the gate")
                if isinstance(s, dict) and col_listed(s.get("c")):
                    reasons.append(f"protected taxon: {path}: species[{i}] carries a masked col_key")
                if isinstance(s, dict) and col_listed(s.get("lk")):
                    reasons.append(f"protected taxon: {path}: species[{i}] carries a masked legacy key")
            for i, p in enumerate(doc.get("possible", []) if isinstance(doc.get("possible"), list) else []):
                if isinstance(p, dict) and listed(p.get("n"), "SPECIES"):
                    reasons.append(f"protected taxon: {path}: possible[{i}] is masked or quarantined by the gate")
                if isinstance(p, dict) and col_listed(p.get("c")):
                    reasons.append(f"protected taxon: {path}: possible[{i}] carries a masked col_key")
                if isinstance(p, dict) and col_listed(p.get("lk")):
                    reasons.append(f"protected taxon: {path}: possible[{i}] carries a masked legacy key")
        elif path == "mesh-research.json" and isinstance(doc, dict):
            for i, row in enumerate(doc.get("genera", []) if isinstance(doc.get("genera"), list) else []):
                if isinstance(row, list) and row and listed(row[0], "GENUS"):
                    reasons.append(f"protected taxon: {path}: genera[{i}] is in a masked genus")
        elif path.startswith("species/") and isinstance(doc, dict):
            # TASK-024: no masked key, legacy key or name anywhere under species/ —
            # masked species get no entry, no marker, no link (spec section 1).
            for i, e in enumerate(doc.get("species", []) if isinstance(doc.get("species"), list) else []):
                if isinstance(e, dict) and listed(e.get("name"), "SPECIES"):
                    reasons.append(f"protected taxon: {path}: species[{i}] is masked or "
                                   f"quarantined by the gate")
                if isinstance(e, dict) and col_listed(e.get("key")):
                    reasons.append(f"protected taxon: {path}: species[{i}] carries a masked key")
                if isinstance(e, dict) and col_listed(e.get("legacy_key")):
                    reasons.append(f"protected taxon: {path}: species[{i}] carries a masked "
                                   f"legacy key")


# ---------- validation ------------------------------------------------------------------------

def _load_all(staging, files, reasons):
    docs, gz = {}, {}
    for rel in files:
        with open(os.path.join(staging, rel), "rb") as fh:
            raw = fh.read()
        try:
            docs[rel] = json.loads(raw)
        except ValueError as e:
            reasons.append(f"shape: {rel}: does not parse as JSON ({e})")
        gz[rel] = len(gzip.compress(raw, 9))
    return docs, gz


def validate(staging, gate_args=None):
    """(reasons, gzip sizes) — an empty reason list means publishable."""
    reasons = []
    files = sorted(os.path.relpath(os.path.join(root, n), staging).replace(os.sep, "/")
                   for root, _, names in os.walk(staging) for n in names)
    docs, gz = _load_all(staging, files, reasons)

    if "dashboard.json" in files:
        _occurrence(files, docs, staging, gate_args, reasons)
    elif "japan-outline.json" in files:
        for rel in files:
            if rel != "japan-outline.json":
                reasons.append(f"stray file: {rel} is not part of the geography-only bundle")
        check_outline(docs.get("japan-outline.json"), "japan-outline.json", reasons)
    else:
        reasons.append("allowlist: no publishable assets — expected dashboard.json (+ areas/) "
                       "or geography-only japan-outline.json")

    for rel in files:                                   # the prose scan covers every asset
        for s in iter_strings(docs.get(rel)):
            low = s.lower()
            for pat in PROSE_PATTERNS:
                if pat in low:
                    reasons.append(f"unsafe field: {rel}: string matches known prose pattern {pat!r}")
                    break
    for rel in files:
        cap = BUDGETS.get(rel, DETAIL_BUDGET if rel.startswith(("areas/", "species/")) else None)
        if cap is not None and gz[rel] > cap * KIB:
            reasons.append(f"over budget: {rel}: {gz[rel]} bytes gzip-9 > {cap} KiB cap")
    return reasons, gz


def _occurrence(files, docs, staging, gate_args, reasons):
    ix = docs.get("areas/index.json")
    ids = check_index(ix, "areas/index.json", reasons) if isinstance(ix, dict) else {}
    six = docs.get("species/index.json")
    allowed = {"dashboard.json", "areas/index.json", "japan-outline.json", "mesh-research.json"} \
        | {f"areas/{i}.json" for i in ids}
    if isinstance(six, dict) and isinstance(six.get("files"), dict):
        allowed |= {"species/index.json"} | {f"species/{s}.json" for s in six["files"]}
    for rel in files:
        if rel not in allowed:
            reasons.append(f"stray file: {rel} is not part of the publish bundle")
    for aid in ids:
        if f"areas/{aid}.json" not in files:
            reasons.append(f"missing area file: areas/{aid}.json is in the index but not on disk")
    if any(rel.startswith("species/") for rel in files):
        check_species(files, docs, staging, reasons, ids)

    stamps = set()
    for rel in [r for r in files if r == "dashboard.json" or r == "mesh-research.json"
                or (r.startswith("areas/") and r.endswith(".json"))]:
        d = docs.get(rel)
        if not isinstance(d, dict):
            continue
        if d.get("schema_version") != 1:
            reasons.append(f"schema_version: {rel}: {d.get('schema_version')!r} is not 1 "
                           f"(unknown or missing version rejects per asset)")
        snap = d.get("snapshot")
        if not isinstance(snap, str) or not HEX64.match(snap):
            reasons.append(f"snapshot: {rel}: {snap!r} is not a 64-hex lowercase digest")
        else:
            stamps.add(snap)
        if rel == "dashboard.json":
            check_dashboard(d, rel, reasons)
        elif rel == "areas/index.json":
            pass                                    # shape + cells + ids already checked above
        elif rel == "mesh-research.json":
            check_research(d, rel, reasons, ids)
        else:
            check_area_file(d, rel, reasons, ids)
    # TASK-021: the Overview aggregates are additive per file but uniform across the
    # bundle — the area inspector reads them for every area or for none.
    detail = [r for r in files if r.startswith("areas/") and r.endswith(".json")
              and r != "areas/index.json"]
    carrying = [r for r in detail if isinstance(docs.get(r), dict) and AGG_KEYS <= set(docs[r])]
    if carrying and len(carrying) != len(detail):
        reasons.append(f"integrity: TASK-021 aggregates on {len(carrying)} of {len(detail)} area files — "
                       f"all or none (missing from {sorted(set(detail) - set(carrying))[:3]})")
    # PR #5 review: a group's aggregates are defined as the sum of its members' published
    # arrays — restamped counts that contradict the leaf areas must not publish
    for r in carrying:
        d = docs[r]
        members = d.get("members") if isinstance(d, dict) else None
        if not isinstance(members, list) or not members:
            continue
        mdocs = [docs.get(f"areas/{m}.json") for m in members]
        if not all(isinstance(md, dict) and AGG_KEYS <= set(md) for md in mdocs):
            continue        # a member without the trio fails the uniformity check above
        for field in ("evidence", "id_depth", "decades"):
            merged = {}
            for md in mdocs:
                for row in md[field]:
                    if isinstance(row, list) and len(row) == 2 and isinstance(row[1], int):
                        merged[row[0]] = merged.get(row[0], 0) + row[1]   # malformed rows already
            if dict(d[field]) != merged:                                  # rejected by the shape check
                reasons.append(f"integrity: {r}: {field} disagrees with the sum of its members' arrays")
    if len(stamps) > 1:
        reasons.append("digest mismatch: occurrence files carry more than one snapshot value")

    dash = docs.get("dashboard.json")
    if isinstance(dash, dict) and isinstance(ix, dict) and isinstance(ix.get("cells"), dict):
        keys = []
        for i, row in enumerate(dash.get("mesh", []) if isinstance(dash.get("mesh"), list) else []):
            k = mesh_cell(row[0], row[1]) if isinstance(row, list) and len(row) == 4 else None
            keys.append(k)
            if k is None or cell_center(*k) != [row[0], row[1]]:
                reasons.append(f"rectangle: dashboard.json: mesh[{i}] centre "
                               f"({row[0] if isinstance(row, list) and len(row) == 4 else '?'}, "
                               f"{row[1] if isinstance(row, list) and len(row) > 1 else '?'}) "
                               f"does not reconstruct to its key's rectangle")
        mesh_keys = {f"{r}:{c}" for r, c in (k for k in keys if k)}
        if len(mesh_keys) != len(keys):
            reasons.append("rectangle: dashboard.json: duplicate mesh key")
        if mesh_keys != set(ix["cells"]):
            reasons.append(f"mesh/index key mismatch: dashboard mesh has {len(mesh_keys)} keys, "
                           f"index cells has {len(ix['cells'])}; symmetric difference "
                           f"{sorted(mesh_keys ^ set(ix['cells']))[:5]}")
        digest = bundle_digest(staging)
        for rel, d in docs.items():
            # species/ carries its own digest (check_species); it never joins the
            # occurrence bundle's, exactly as japan-outline.json does not.
            if rel in allowed and rel != "japan-outline.json" and not rel.startswith("species/") \
                    and isinstance(d, dict) \
                    and isinstance(d.get("snapshot"), str) and HEX64.match(d["snapshot"]) \
                    and d["snapshot"] != digest:
                reasons.append(f"digest mismatch: {rel}: stamped snapshot != recomputed bundle digest")

    outline = docs.get("japan-outline.json")
    if isinstance(outline, dict):
        check_outline(outline, "japan-outline.json", reasons)
        for i, p in enumerate(outline.get("places", [])
                              if isinstance(outline.get("places"), list) else []):
            if isinstance(p, dict) and isinstance(p.get("area_id"), str) and p["area_id"] not in ids:
                reasons.append(f"integrity: japan-outline.json: places[{i}].area_id is not in the "
                               f"area index (a label may only select through it)")
    if isinstance(ix, dict):
        for row in ix.get("areas", []) if isinstance(ix.get("areas"), list) else []:
            if isinstance(row, dict) and row.get("kind") == "group":
                f = docs.get(f"areas/{row['id']}.json")
                if isinstance(f, dict) and f.get("members") != row.get("members"):
                    reasons.append(f"integrity: areas/{row['id']}.json members != index members")

    try:
        gate = load_gate(**(gate_args or {}))
    except Exception as e:                              # PublicTaxaError: unverifiable protection blocks
        reasons.append(f"protected-taxon gate: reference inputs failed to load, protection cannot "
                       f"be verified: {e}")
    else:
        gate_scan(docs, gate, reasons)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Validate a staging directory as publishable "
                                             "(plan §6/§7). Exit 1 with the reason list on failure.")
    ap.add_argument("staging", help="staging directory to validate")
    ap.add_argument("--mask", default="data/reference/taxon_mask.csv")
    ap.add_argument("--coverage", default="data/reference/redlist_coverage.csv")
    ap.add_argument("--exceptions", default="data/reference/match_exceptions.csv")
    ap.add_argument("--redlist", default="data/raw/redlist/redlist_fungi_jp.json")
    ap.add_argument("--col-mask-map", default="data/reference/col_mask_map.csv",
                    help="required input (TASK-018): COL XR mask map; missing blocks validation")
    args = ap.parse_args(argv)
    gate_args = {k: getattr(args, k) for k in ("mask", "coverage", "exceptions", "redlist")}
    gate_args["col_mask"] = args.col_mask_map
    reasons, gz = validate(args.staging, gate_args)
    if reasons:
        print(f"{args.staging}: NOT publishable — {len(reasons)} reason(s):", file=sys.stderr)
        for r in reasons:
            print(f"  - {r}", file=sys.stderr)
        return 1
    print(f"{args.staging}: publishable — {len(gz)} files, {sum(gz.values()):,} bytes gzip-9 total, "
          f"largest file {max(gz.values()):,} bytes gzip-9")
    return 0


if __name__ == "__main__":
    sys.exit(main())
