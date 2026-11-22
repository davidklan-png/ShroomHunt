#!/usr/bin/env python3
"""Build mesh-research.json, the gated research payload (plan §5 row 3A, §6 Research, §7).

Banded month × kind aggregates per JIS 2nd mesh plus the reviewed genus search
list. No cell gains precision beyond the bands of §6: counts map to
10–24, 25–49, 50–99, 100–249, 250+ (bands 0–4) and genus share to
0–<20, 20–<40, 40–<60, 60–<80, 80–100% (bands 0–4). Only jp records enter:
the East Asia neighbour table keeps its native 1° cells outside the JIS mesh
and is candidate evidence, never a research slice.

Definitions this module owns (§7 gates the dashboard UI trusts):

* Deduplication happens before any counting, on the stable source ID the
  table actually carries: jp.gbifID when present (every GBIF SIMPLE_CSV build
  has it), else jp.occurrenceID. Duplicates are exact repeats of one source
  record, so the first copy in gbifID order wins; a null ID identifies
  nothing and each such row counts once.
* A sampling event is a distinct non-null (datasetKey, eventID) pair; when a
  record's eventID is missing, (datasetKey, recordedBy) stands in for that
  record. Records with neither contribute no event.
* Eligible = gate-public (pipeline/public_taxa.py) AND spatially eligible
  (pipeline/eligibility.py) — the same two gates every spatial output uses.
* Support gate, per cell and slice: ≥10 eligible records AND ≥3 independent
  events, else the cell is omitted from that slice (missing cell = no public
  estimate). A slice with no publishable cell is omitted (§6: a missing slice
  means the combination was withheld).
* Genus share: the two sides are the taxonRank='GENUS' records and the rest.
  Both sides need ≥5 observations, else genus_share_band is null (count band
  may still ship).
* Complementary suppression: a cell is dropped from month 0 of a kind (the
  rule is uniform across all/m/l/o) when at least 10 of that kind's 12
  single-month slices publish it. Ten-plus published months leave at most two
  unknown months (plus monthless records) in the all-months total, and with
  count bands five wide, differencing published months against it bounds the
  remainder tightly enough to isolate a small group. Fewer published months
  leave the remainder spread too thin to difference, so the cell ships.
* --previous mesh-research.json: a cell whose count or share band differs
  from that published file is omitted from that slice entirely (holding both
  files must not narrow either range). These drops happen before the month-0
  suppression count, so a previous-dropped month no longer counts as
  published for the 10-of-12 threshold.
* Genus search list: only genera on the reviewed name table
  (data/reference/taxon_names.csv, columns genus,label_en,label_ja,aliases —
  David's review maintains it; the rows are the plan §7 genus-search gate).
  Per genus: ≥10 eligible genus-rank records across ≥3 cells; per listed leaf
  area (island/tile via the islands.csv priority order, groups never) ≥3
  independent events. Each surviving row ships its names as the optional
  third element [label_en, label_ja, *aliases] (empty ones dropped). Masking
  is species-level only (David's ruling 2026-11-22): a genus row is built from
  eligible records after its protected species were individually masked, so
  no genus is excluded by name. An absent or header-only
  (unreviewed) table ships genera: [] with a stderr warning while the slices
  still build.

Usage: python3 pipeline/build_mesh_research.py [--db data/shroomhunt.duckdb]
         [--out data/processed/mesh-research.json] [--previous mesh-research.json]
         [--taxon-names data/reference/taxon_names.csv] ...
Output is deterministic bytes (snapshot.write_json); snapshot stays empty
until pipeline/snapshot.py stamps the bundle. Logs are aggregate counts only.
"""
import argparse, csv, json, os, sys
from collections import defaultdict
import duckdb

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from geo_ja import cell_key, mesh_cell, tile_id  # noqa: E402
from public_taxa import load_gate  # noqa: E402
from eligibility import precision_selection, spatially_eligible  # noqa: E402
from snapshot import write_json  # noqa: E402

# kind() and its tables are copied verbatim from build_areas.py: its top-level
# CLI makes a clean import impossible. Keep the two copies in sync (§5 row 3A
# says reuse the classifier, never fork it silently).
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

MIN_RECORDS, MIN_EVENTS, MIN_SHARE_SIDE = 10, 3, 5
GENUS_MIN_RECORDS, GENUS_MIN_CELLS, GENUS_MIN_EVENTS = 10, 3, 3
KIND_ORDER = ("all", "m", "l", "o")
COUNT_EDGES = (10, 25, 50, 100, 250)   # band n covers [edge_n, edge_{n+1}); 250+ is band 4

ap = argparse.ArgumentParser()
ap.add_argument("--db", default="data/shroomhunt.duckdb")
ap.add_argument("--out", default="data/processed/mesh-research.json")
ap.add_argument("--redlist", default="data/raw/redlist/redlist_fungi_jp.json")
ap.add_argument("--mask", default="data/reference/taxon_mask.csv")
ap.add_argument("--coverage", default="data/reference/redlist_coverage.csv")
ap.add_argument("--exceptions", default="data/reference/match_exceptions.csv")
ap.add_argument("--islands", default="data/reference/islands.csv")
ap.add_argument("--taxon-names", default="data/reference/taxon_names.csv")
ap.add_argument("--genera-allowlist", help="deprecated no-op: renamed --taxon-names (TASK-008)")
ap.add_argument("--previous", help="previously published mesh-research.json (differencing guard)")
a = ap.parse_args()

c = duckdb.connect(a.db)
gate = load_gate(mask=a.mask, coverage=a.coverage, exceptions=a.exceptions, redlist=a.redlist)
elig_join, sel = precision_selection(c)

jp_cols = {r[0] for r in c.execute(
    "select column_name from information_schema.columns where table_name='jp'").fetchall()}
DEDUP = next((k for k in ("gbifID", "occurrenceID") if k in jp_cols), None)
def col(n): return f"jp.{n}" if n in jp_cols else "null"
rows = c.execute(f"""
  select {col('speciesKey')}, {col('species')}, {col('genus')}, {col('family')}, {col('taxonRank')},
         {col('class')}, try_cast({col('month')} as int), try_cast({col('decimalLatitude')} as double),
         try_cast({col('decimalLongitude')} as double), {col('datasetKey')}, {col('eventID')},
         {col('recordedBy')}, {col(DEDUP) if DEDUP else 'null'}, {", ".join(sel)}
  from jp {elig_join}""" + (f" order by jp.{DEDUP} nulls last" if DEDUP else "")).fetchall()
audit = gate.audit((r[1], r[0], r[2], r[4]) for r in rows)

# ---------- eligible records into joint month x kind slices ---------------------------------
slices = defaultdict(lambda: defaultdict(lambda: [0, set(), 0]))   # (month, kind) -> cell -> [n, events, genus-rank n]
genus_hits = defaultdict(list)                                     # folded genus -> [(cellkey, event)]
seen, dupes = set(), 0
for r in rows:
    sk, sp, gen, fam, rank, cls, mon, lat, lon, dsk, eid, rby, sid, unc, prec, iss, genz, held = r
    if sid is not None and str(sid).strip():
        s = str(sid).strip()
        if s in seen:
            dupes += 1
            continue
        seen.add(s)
    if not gate.is_public(species=sp, species_key=sk, genus=gen, taxon_rank=rank): continue
    cell = mesh_cell(lat, lon)
    if cell is None or not spatially_eligible(unc, prec, iss, genz, held): continue
    ev = None
    if eid is not None and str(eid).strip(): ev = (str(dsk) if dsk is not None else None, str(eid).strip())
    elif rby is not None and str(rby).strip(): ev = (str(dsk) if dsk is not None else None, str(rby).strip())
    months = (0, mon) if mon is not None and 1 <= mon <= 12 else (0,)
    kd = kind(cls, fam, gen or (sp or "").split(" ")[0])
    for m in months:                       # one record counts in its month slice and month 0
        for k in ("all", kd):              # ... and in its kind and in 'all'
            e = slices[(m, k)][cell]
            e[0] += 1
            if ev: e[1].add(ev)
            if rank == "GENUS": e[2] += 1
    if rank == "GENUS" and gen and gen.strip():
        genus_hits[gen.strip().lower()].append((cell_key(*cell), ev))

def count_band(n):
    return 4 if n >= 250 else sum(n >= e for e in COUNT_EDGES) - 1

# ---------- support gate ------------------------------------------------------------------------
published = {}
for (m, k), cells_ in slices.items():
    out = {}
    for cell, (n, events, gn) in cells_.items():
        if n < MIN_RECORDS or len(events) < MIN_EVENTS:
            continue
        ng = n - gn
        out[cell_key(*cell)] = [count_band(n),
                                min(4, gn * 5 // n) if gn >= MIN_SHARE_SIDE and ng >= MIN_SHARE_SIDE else None]
    if out: published[(m, k)] = out

# ---------- differencing guard against the previous published file -----------------------------
moved = 0
if a.previous:
    with open(a.previous, encoding="utf-8") as f:
        pm = {(s[0], s[1]): {cl[0]: (cl[1], cl[2]) for cl in s[2]}
              for s in json.load(f).get("slices", [])}
    for sk in list(published):
        if sk not in pm: continue
        for key in list(published[sk]):
            was = pm[sk].get(key)
            if was is not None and tuple(was) != tuple(published[sk][key]):
                del published[sk][key]
                moved += 1

# ---------- complementary suppression: month 0 (isolation threshold 10 of 12) ------------------
suppressed = 0
for k in KIND_ORDER:
    if (0, k) not in published: continue
    pub_months = defaultdict(int)          # cell -> single-month slices of kind k that publish it
    for (m, kk), cells_ in published.items():
        if kk == k and 1 <= m <= 12:
            for key in cells_: pub_months[key] += 1
    for key in [key for key in published[(0, k)] if pub_months[key] >= 10]:
        del published[(0, k)][key]         # <=2 unknown months against 5-wide bands: boundable, so withhold
        suppressed += 1
    if not published[(0, k)]: del published[(0, k)]

# ---------- reviewed genus search list -----------------------------------------------------------
def read_taxon_names(path):
    """(genus, [label_en, label_ja, *aliases]) rows; [] when absent or header-only."""
    try:
        with open(path, newline="", encoding="utf-8") as f:
            lines = [ln for ln in f if not ln.lstrip().startswith("#")]
    except FileNotFoundError:
        return []
    rows = []
    for r in csv.DictReader(lines):
        genus = (r.get("genus") or "").strip()
        if not genus:
            continue
        names = [(r.get(k) or "").strip() for k in ("label_en", "label_ja")]
        names += [al.strip() for al in (r.get("aliases") or "").split("|") if al.strip()]
        rows.append((genus, [n for n in names if n]))
    return rows

if a.genera_allowlist:
    print("mesh-research: --genera-allowlist is a deprecated no-op; the reviewed name table is "
          "--taxon-names", file=sys.stderr)
listed = read_taxon_names(a.taxon_names)
genera, dropped_genera = [], {"support": 0}
if not listed:
    print(f"mesh-research: genus search disabled — taxon name table absent or unreviewed "
          f"({a.taxon_names}); genera stays [] until David's review populates it", file=sys.stderr)
else:
    islands = list(csv.DictReader(open(a.islands, newline="", encoding="utf-8")))
    for i in islands:
        for k in ("lat_min", "lat_max", "lon_min", "lon_max"): i[k] = float(i[k])
    def area_of(key):
        # islands.csv order is the deterministic priority, exactly like build_areas' ownership
        row, col = (int(x) for x in key.split(":"))
        clat, clon = (row + 0.5) / 12, (col + 0.5) / 8
        for i in islands:
            if i["lat_min"] <= clat <= i["lat_max"] and i["lon_min"] <= clon <= i["lon_max"]: return i["id"]
        return tile_id(row, col)
    by_genus = {}
    for g, names in listed:
        by_genus.setdefault(g.lower(), (g, names))
    for g, names in sorted(by_genus.values()):
        hits = genus_hits.get(g.lower(), [])
        if len(hits) < GENUS_MIN_RECORDS or len({cell for cell, _ in hits}) < GENUS_MIN_CELLS:
            dropped_genera["support"] += 1
            continue
        per_area = defaultdict(set)
        for cell, ev in hits:
            if ev: per_area[area_of(cell)].add(ev)
        areas = sorted(aid for aid, evs in per_area.items() if len(evs) >= GENUS_MIN_EVENTS)
        if areas: genera.append([g, areas, names])
        else: dropped_genera["support"] += 1

# ---------- write ---------------------------------------------------------------------------------
def cell_order(kv): return (int(kv[0].split(":")[0]), int(kv[0].split(":")[1]))
slices_out = [[m, k, [[key, *bands] for key, bands in sorted(cells_.items(), key=cell_order)]]
              for (m, k), cells_ in sorted(published.items(),
                                           key=lambda item: (item[0][0], KIND_ORDER.index(item[0][1])))]
write_json(a.out, {"schema_version": 1, "snapshot": "", "slices": slices_out, "genera": genera})
prev_note = f", {moved} cell slice(s) dropped against {os.path.basename(a.previous)}" if a.previous else ""
print(f"wrote {a.out}: {len(slices_out)} slices, {sum(len(s[2]) for s in slices_out)} cell entries, "
      f"{len(genera)} genera; dedupe key {DEDUP or 'none'} skipped {dupes} duplicate source records")
print(f"gate audit (aggregate): {audit}; suppression: {suppressed} cell(s) dropped from month 0"
      f"{prev_note}; genera dropped: {dropped_genera}")
