#!/usr/bin/env python3
"""Build the per-genus species bundles and their index (TASK-024, spec item 7).

For every genus with at least one PUBLIC species: species/<genus-slug>.json
carrying our own facts plus the reviewed external enrichment; species/index.json
carries the snapshot digest, the build date, the counts and a sha256/size/species
entry per file. Genus files are canonical JSON with no timestamps inside, so an
unchanged genus rewrites to identical bytes and a refresh touches only what
changed (the file is left untouched byte-for-byte). Beside the index,
species/summary.json (TASK-025, item 9) is the national per-species ranked list
[name, col_key, legacy_key, area_count, record_count, mhlw, endemic] the map
disclosure reads; the index carries its sha256 as summary_sha256 so it is
covered without joining the genus digest.

Our own facts per species (spec section 1, no edibility anywhere): COL key +
legacy key, names and classification, record count by evidence tier (all gated
Japan records), fruiting months (MATERIAL_SAMPLE excluded), area ids from the
BUILT area files (the published truth, including name-routed species; island and
tile ids only — groups are derivable from their members), elevation band and
host genera from the DwC-A jp_extra, the neighbouring-East-Asia verdict from the
ea cells, the areas listing it as a could-occur candidate (the area files'
possible lists, inverted), and the first/last record decade.

External facts come from data/reference/enrichment.csv (pipeline/enrich.py):
every field nullable, a missing row or file leaves them empty — never blocks a
build. A photo needs author AND licence AND licence URL AND file page AND an
acceptable licence class (CC0 / CC BY / CC BY-SA, re-checked here so a hand-edited
CSV cannot smuggle a non-free image past the spec section 2 rule).

Masked species get no entry, no marker, no link: the same shared gate as every
public output (public_taxa.load_gate, col_mask_map fail-closed on a COL-keyed DB).

Usage: python3 pipeline/build_species_bundles.py --db data/shroomhunt.duckdb
         --out staging/species [--areas staging/areas]
         [--enrich data/reference/enrichment.csv]  [gate inputs]
"""
import argparse, csv, hashlib, json, os, re, sys, time, urllib.parse
from collections import Counter

import duckdb

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from public_taxa import SPECIES_RANKS, load_gate  # noqa: E402
from col_xr import ensure, exprs, keyed  # noqa: E402
from shared import dedup_temp_jp, kind  # noqa: E402
from snapshot import canonical  # noqa: E402
from sources.wikidata import ACCEPTABLE, classify_licence  # noqa: E402

# Evidence codes as in build_areas' species rows (s specimen, d material sample,
# o everything else); duplicated here because build_areas keeps it inside main().
EVID = {"PRESERVED_SPECIMEN": "s", "MATERIAL_SAMPLE": "d", "LIVING_SPECIMEN": "s",
        "MATERIAL_CITATION": "s"}
DECADE_WINDOW = (1850, 2100)     # the national decades window of the overview aggregates
HOST_STOP = {"on", "in", "near", "and", "with", "dead", "living", "rotten", "wood",
             "soil", "host", "the", "of", "unknown", "associated"}
PHOTO_COLS = ("photo_thumb_url", "photo_file_page", "photo_author",
              "photo_licence", "photo_licence_url")


def slugify(genus):
    return re.sub(r"[^a-z0-9]+", "-", genus.lower()).strip("-")


def host_genera(assoc_text):
    """Capitalized Latin first tokens of the associatedTaxa entries — one genus
    per entry ('Quercus serrata | Fagus crenata' -> Quercus, Fagus)."""
    out = []
    for part in re.split(r"[|;,]", assoc_text or ""):
        for tok in re.findall(r"[A-Za-z]{3,}", part):
            if tok[0].isupper() and tok[1:].islower() and tok.lower() not in HOST_STOP:
                out.append(tok)
                break
    return out


def name_ja_from(url):
    """The ja Wikipedia title back out of its URL (underscores -> spaces)."""
    if not url or "/" not in url:
        return None
    title = urllib.parse.unquote(url.rsplit("/", 1)[-1]).replace("_", " ").strip()
    return title or None


def load_enrichment(path):
    """{sp_key: row} from enrichment.csv, {} when absent — external facts degrade
    to empty, a missing table never blocks the build."""
    if not os.path.exists(path):
        print(f"species: {path} missing; external fields stay empty for every species",
              file=sys.stderr)
        return {}
    with open(path, newline="", encoding="utf-8") as f:
        lines = [ln for ln in f if not ln.lstrip().startswith("#")]
    return {r["sp_key"]: r for r in csv.DictReader(lines)}


def external_for(sp_key, rows):
    """The species' external block: every field nullable, photo only when complete
    and licence-acceptable (spec section 2, re-checked at this last hop)."""
    r = rows.get(sp_key) or {}
    g = lambda c: (r.get(c) or "").strip() or None  # noqa: E731
    photo = None
    if all(g(c) for c in PHOTO_COLS) \
            and classify_licence(g("photo_licence"), g("photo_licence_url")) in ACCEPTABLE:
        photo = {c[len("photo_"):]: g(c) for c in PHOTO_COLS}
    literature = []
    if g("literature"):
        try:
            literature = json.loads(g("literature"))[:5]
        except ValueError:
            print(f"species: enrichment literature for {sp_key} does not parse; omitted",
                  file=sys.stderr)
    return {"wikidata_qid": g("wikidata_qid"), "wikipedia_ja": g("wikipedia_ja"),
            "wikipedia_en": g("wikipedia_en"), "photo": photo, "mhlw_url": g("mhlw_url"),
            "endemic_hosoya2016": g("endemic_hosoya2016") == "1",
            "literature": literature, "mushroomexpert_url": g("mushroomexpert_url"),
            "mushroomexpert_level": g("mushroomexpert_level")}


def invert_areas(areas_dir):
    """(recorded, could_occur): sp_key -> sorted area ids, from the BUILT area
    files — exactly what the area view publishes. Island and tile ids only."""
    recorded, could = {}, {}
    idx_path = os.path.join(areas_dir, "index.json")
    if not os.path.exists(idx_path):
        print(f"species: {areas_dir} has no index.json; area_ids and could_be_found_area_ids "
              f"stay empty (build areas first)", file=sys.stderr)
        return recorded, could
    with open(idx_path, encoding="utf-8") as f:
        ids = [r["id"] for r in json.load(f)["areas"] if r["kind"] != "group"]
    for aid in sorted(ids):
        with open(os.path.join(areas_dir, f"{aid}.json"), encoding="utf-8") as f:
            doc = json.load(f)
        for target, rows in ((recorded, doc["species"]), (could, doc["possible"])):
            for s in rows:
                k = s.get("c") or ("L" + s["lk"] if s.get("lk") else None)
                if k:
                    target.setdefault(k, set()).add(aid)
    return {k: sorted(v) for k, v in recorded.items()}, {k: sorted(v) for k, v in could.items()}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Build per-genus species bundles + index "
                                             "(spec item 7).")
    ap.add_argument("--db", default="data/shroomhunt.duckdb")
    ap.add_argument("--out", default="data/processed/species")
    ap.add_argument("--areas", default="data/processed/areas",
                    help="the BUILT area directory (build_areas.py --out); area_ids and "
                         "could_be_found_area_ids invert its published tables")
    ap.add_argument("--enrich", default="data/reference/enrichment.csv")
    ap.add_argument("--mask", default="data/reference/taxon_mask.csv")
    ap.add_argument("--coverage", default="data/reference/redlist_coverage.csv")
    ap.add_argument("--exceptions", default="data/reference/match_exceptions.csv")
    ap.add_argument("--redlist", default="data/raw/redlist/redlist_fungi_jp.json")
    ap.add_argument("--col-mask-map", default="data/reference/col_mask_map.csv")
    a = ap.parse_args(argv)

    con = duckdb.connect(a.db)
    try:
        ensure(con)
        gate = load_gate(mask=a.mask, coverage=a.coverage, exceptions=a.exceptions,
                         redlist=a.redlist, col_mask=a.col_mask_map if keyed(con) else None)
        tables = {r[0] for r in con.execute(
            "select table_name from information_schema.tables").fetchall()}
        jp_cols = {r[0] for r in con.execute(
            "select column_name from information_schema.columns where table_name='jp'").fetchall()}
        X = exprs("jp", jp_cols)
        gid = "jp.gbifID" if "gbifID" in jp_cols else "null"
        extra = ""
        if "jp_extra" in tables:
            extra = f" left join jp_extra xe on {gid} = xe.gbifID"
            elev, assoc = "xe.elevation", "xe.assoc"
        else:
            elev, assoc = "null", "null"
            print("species: no jp_extra table; elevation_band and host_genera stay null",
                  file=sys.stderr)
        # PR #7 review: cross-dataset specimen dedup, the same TASK-016 idiom the
        # dashboard and area builders apply, so tier/month counts match published semantics
        dropped = dedup_temp_jp(con)
        if dropped:
            print(f"species bundles: {dropped} cross-dataset duplicates dropped",
                  file=sys.stderr)
        rows = con.execute(f"""select {X['sp_key']}, {X['col_key']}, {X['sp_name']},
                                      {X['family']}, {X['order']}, {X['class']}, {X['genus']},
                                      jp.taxonRank, jp.basisOfRecord, try_cast(jp.month as int),
                                      try_cast(jp.year as int), jp.speciesKey, {elev}, {assoc}
                               from jp{extra}
                               where {X['sp_key']} is not null
                                 and jp.taxonRank in ('SPECIES', 'VARIETY', 'SUBSPECIES', 'FORM')
                                 and {X['sp_name']} like '% %'""").fetchall()
        east = set()
        if "ea" in tables:
            ea_cols = {r[0] for r in con.execute(
                "select column_name from information_schema.columns where table_name='ea'").fetchall()}
            E = exprs("ea", ea_cols)
            for key, sp, colk in con.execute(
                    f"""select speciesKey, {E['sp_name']}, {E['col_key']} from ea
                        where speciesKey is not null""").fetchall():
                if gate.is_public(species=sp, species_key=key, col_key=colk):
                    east.add(f"L{key}" if not colk else colk)
    finally:
        con.close()

    # aggregate our own facts per species identity (all gated Japan records)
    agg = {}
    masked = 0
    for spk, colk, name, fam, order, cls, gen, rank, bor, mon, yr, skey, elev, assoc in rows:
        if not gate.is_public(species=name, species_key=skey, genus=gen, taxon_rank=rank,
                              col_key=colk):
            masked += 1
            continue
        d = agg.setdefault(spk, {"colk": colk, "names": set(), "fam": set(), "order": set(),
                                 "cls": set(), "gen": set(), "legacy": set(),
                                 "tier": Counter(), "months": [0] * 12, "years": set(),
                                 "elev": [], "hosts": Counter()})
        for val, field in ((name, "names"), (fam, "fam"), (order, "order"), (cls, "cls"),
                           (gen, "gen")):
            if val:
                d[field].add(val)
        if not colk and skey is not None:
            d["legacy"].add(str(skey))
        ev = EVID.get(bor, "o")
        d["tier"][ev] += 1
        if ev != "d" and mon is not None and 1 <= mon <= 12:      # fruiting evidence only
            d["months"][mon - 1] += 1
        if yr is not None and DECADE_WINDOW[0] <= yr <= DECADE_WINDOW[1]:
            d["years"].add(yr // 10 * 10)
        if elev is not None:
            d["elev"].append(elev)
        for genus in host_genera(assoc):
            d["hosts"][genus] += 1

    enrich_rows = load_enrichment(a.enrich)
    recorded, could_occur = invert_areas(a.areas)

    entries, genus_of = {}, {}
    for spk in sorted(agg):
        d = agg[spk]
        pick = lambda field: min(d[field]) if d[field] else None  # noqa: E731
        name = pick("names")
        if not name:
            print(f"species: {spk} has no display name; no entry", file=sys.stderr)
            continue
        genus = pick("gen") or name.split(" ")[0]
        cls, fam = pick("cls"), pick("fam")
        hosts = [g for g, _ in sorted(d["hosts"].items(), key=lambda kv: (-kv[1], kv[0]))[:3]]
        genus_of[spk] = genus
        entries[spk] = {
            "key": d["colk"],
            "legacy_key": min(d["legacy"]) if not d["colk"] and d["legacy"] else None,
            "name": name,
            "name_ja": name_ja_from((enrich_rows.get(spk) or {}).get("wikipedia_ja")),
            "family": fam or "",
            "order": pick("order") or "",
            "class": cls or "",
            "kind": kind(cls, fam, genus),
            "records_by_tier": dict(sorted(d["tier"].items())),
            "fruiting_months": d["months"],
            "area_ids": recorded.get(spk, []),
            "elevation_band": [round(min(d["elev"])), round(max(d["elev"]))] if d["elev"] else None,
            "host_genera": hosts or None,
            "east_asia": spk in east,
            "could_be_found_area_ids": could_occur.get(spk, []),
            "first_decade": min(d["years"]) if d["years"] else None,
            "last_decade": max(d["years"]) if d["years"] else None,
            "external": external_for(spk, enrich_rows),
        }

    genera = {}
    for spk, e in entries.items():
        genera.setdefault(genus_of[spk], []).append(e)
    files = {}
    for genus in sorted(genera):
        slug = slugify(genus)
        doc = {"schema_version": 1, "genus": genus,
               "species": sorted(genera[genus], key=lambda e: e["name"])}
        raw = (canonical(doc) + "\n").encode("utf-8")
        path = os.path.join(a.out, f"{slug}.json")
        if not (os.path.exists(path) and open(path, "rb").read() == raw):
            os.makedirs(a.out, exist_ok=True)
            with open(path, "wb") as fh:
                fh.write(raw)
        files[slug] = {"sha256": hashlib.sha256(raw).hexdigest(),
                       "species": len(doc["species"]), "bytes": len(raw)}
    for name in (os.listdir(a.out) if os.path.isdir(a.out) else []):  # lost every species
        if name.endswith(".json") and name[:-5] not in files \
                and name not in ("index.json", "summary.json"):
            os.remove(os.path.join(a.out, name))
            print(f"species: removed stale {name}")

    digest = hashlib.sha256()
    for slug in sorted(files):
        with open(os.path.join(a.out, f"{slug}.json"), "rb") as fh:
            digest.update(fh.read())
    # TASK-025 (item 9): the national per-species summary — every public species
    # ranked by area count, the month-less backbone the map disclosure reads. A
    # sibling of the index, byte-stable like the genus files and covered by the
    # index's summary_sha256 instead of the species digest (a refresh stays cheap).
    summary = {"schema_version": 1, "snapshot": digest.hexdigest(),
               "generated": time.strftime("%Y-%m-%d"),
               "species": sorted(([e["name"], e["key"], e["legacy_key"], len(e["area_ids"]),
                                   sum(e["records_by_tier"].values()),
                                   bool(e["external"]["mhlw_url"]),
                                   e["external"]["endemic_hosoya2016"]]
                                  for e in entries.values()),
                                  key=lambda r: (-r[3], r[0]))}
    sraw = (canonical(summary) + "\n").encode("utf-8")
    spath = os.path.join(a.out, "summary.json")
    if not (os.path.exists(spath) and open(spath, "rb").read() == sraw):
        os.makedirs(a.out, exist_ok=True)
        with open(spath, "wb") as fh:
            fh.write(sraw)
    index = {"schema_version": 1, "snapshot": digest.hexdigest(),
             "generated": time.strftime("%Y-%m-%d"), "genera": len(files),
             "species": sum(f["species"] for f in files.values()), "files": files,
             "summary_sha256": hashlib.sha256(sraw).hexdigest()}
    os.makedirs(a.out, exist_ok=True)
    with open(os.path.join(a.out, "index.json"), "w", newline="\n", encoding="utf-8") as f:
        f.write(canonical(index) + "\n")

    n = len(entries)
    ext = lambda field: sum(1 for e in entries.values()  # noqa: E731
                            if e["external"][field])
    print(f"{a.out}: {len(files)} genus files, {n} species (public), "
          f"{masked} masked records excluded, "
          f"{sum(f['bytes'] for f in files.values()):,} bytes genus files; "
          f"summary {len(summary['species'])} rows / {len(sraw):,} bytes; "
          f"photo {ext('photo')}, mhlw {ext('mhlw_url')}, endemic "
          f"{sum(1 for e in entries.values() if e['external']['endemic_hosoya2016'])}, "
          f"literature {ext('literature')}, mushroomexpert {ext('mushroomexpert_url')}, "
          f"east_asia {sum(1 for e in entries.values() if e['east_asia'])}")


if __name__ == "__main__":
    main()
