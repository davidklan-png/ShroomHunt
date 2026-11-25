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
covered without joining the genus digest. species/names.json (TASK-027,
decision S2) is the name search index: row i belongs to summary row i (the
same order, no names repeated — they cost ~125 KiB gzip-9 on their own) and
carries [name_ja, genus_slug, family, [synonyms…]]; the index carries its
sha256 as names_sha256. species/squares.json (TASK-028) is the map's square
index, one [MeshKey, [summary row indices…]] row per ~10 km square, a species
placed only through a record the dashboard mesh would count there (the shared
source-precision eligibility, pipeline/eligibility.py); no name is in the
file, so a masked species — it has no summary row — cannot appear. The index
carries its sha256 as squares_sha256. Synonyms come from the COL crosswalk
(--crosswalk, optional: without it every list is empty) and never name a
masked taxon.

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
         [--enrich data/reference/enrichment.csv] [--crosswalk data/raw/col/gbif-col265.tsv.gz]
         [gate inputs]
"""
import argparse, csv, hashlib, json, os, re, sys, time, urllib.parse
from collections import Counter

import duckdb

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from public_taxa import SPECIES_RANKS, load_gate  # noqa: E402
from col_xr import ensure, exprs, keyed, load_map  # noqa: E402
from eligibility import precision_selection, spatially_eligible  # noqa: E402
from geo_ja import mesh_key  # noqa: E402
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
# ponytail: 3 synonyms per species — names.json measures 169 KiB gzip-9 capped vs
# 310 KiB uncapped (37,056 synonyms over ~6,400 species, local DB 2026-11-24); raise
# the cap if name search needs more
SYNONYM_CAP = 3


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


def write_stable(path, raw):
    """Write only when the bytes differ, so an unchanged file is left untouched
    (byte-for-byte, mtime included) and a refresh touches only what changed."""
    same = False
    if os.path.exists(path):
        with open(path, "rb") as fh:
            same = fh.read() == raw
    if not same:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(raw)


def load_synonyms(path):
    """{col_accepted_id: {gbif_name…}} from the COL crosswalk (col_xr.load_map's
    colmap, in an in-memory connection so the DB file is never written): only
    col_status 'synonym' rows — 'ambiguous synonym' and 'misapplied' never count."""
    out = {}
    con = duckdb.connect()
    try:
        load_map(con, path)
        for colk, syn in con.execute("select col_accepted_id, gbif_name from colmap "
                                     "where col_status = 'synonym'").fetchall():
            out.setdefault(colk, set()).add(syn)
    finally:
        con.close()
    return out


def synonyms_for(name, keys, crosswalk, gate):
    """(kept, dropped_by_cap, masked): the display name's crosswalk synonyms under the
    given COL keys — distinct, never the name itself, never one the gate masks.
    Ranked: a binomial under another genus first, then any other binomial, then the
    rest; alphabetical inside each group."""
    found = set()
    for k in keys:
        found |= crosswalk.get(k) or set()
    found.discard(name)
    genus = name.split(" ")[0]

    def group(s):
        head, _, tail = s.partition(" ")
        return 2 if not tail or " " in tail else (0 if head != genus else 1)
    public = [s for s in sorted(found, key=lambda s: (group(s), s)) if gate.is_public(species=s)]
    return public[:SYNONYM_CAP], max(0, len(public) - SYNONYM_CAP), len(found) - len(public)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Build per-genus species bundles + index "
                                             "(spec item 7).")
    ap.add_argument("--db", default="data/shroomhunt.duckdb")
    ap.add_argument("--out", default="data/processed/species")
    ap.add_argument("--areas", default="data/processed/areas",
                    help="the BUILT area directory (build_areas.py --out); area_ids and "
                         "could_be_found_area_ids invert its published tables")
    ap.add_argument("--enrich", default="data/reference/enrichment.csv")
    ap.add_argument("--crosswalk", help="the COL crosswalk TSV.gz (data/raw/col/gbif-col265.tsv.gz); "
                                        "names.json synonyms stay empty without it")
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
        # TASK-028: the dashboard mesh's spatial rule (build_dashboard_data.py), read
        # off the same records so a species sits in a square through exactly the
        # records the map counts there
        elig_join, sel = precision_selection(con)
        rows = con.execute(f"""select {X['sp_key']}, {X['col_key']}, {X['sp_name']},
                                      {X['family']}, {X['order']}, {X['class']}, {X['genus']},
                                      jp.taxonRank, jp.basisOfRecord, try_cast(jp.month as int),
                                      try_cast(jp.year as int), jp.speciesKey, {elev}, {assoc},
                                      try_cast(jp.decimalLatitude as double),
                                      try_cast(jp.decimalLongitude as double), {", ".join(sel)}
                               from jp{extra} {elig_join}
                               where {X['sp_key']} is not null""").fetchall()
        # The rank and "has a space" filters that make a species ENTRY moved to the loop
        # below (PR #16 review): the map counts a square's species from every record with
        # a species key, whatever its rank, so squares.json must too.
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
    squares_of = {}   # TASK-028: sp_key -> {MeshKey: eligible records}
    for spk, colk, name, fam, order, cls, gen, rank, bor, mon, yr, skey, elev, assoc, *geo in rows:
        if not gate.is_public(species=name, species_key=skey, genus=gen, taxon_rank=rank,
                              col_key=colk):
            masked += 1
            continue
        lat, lon, unc, prec, iss, genz, held = geo
        k = mesh_key(lat, lon) if spatially_eligible(unc, prec, iss, genz, held) else None
        if k:
            squares_of.setdefault(spk, Counter())[k] += 1
        if rank not in SPECIES_RANKS or not name or " " not in name:
            continue                                 # places a square, as on the map, but gets no page
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
    # before anything is written: a missing crosswalk fails the build with the
    # output directory untouched (empty synonym lists are only for no --crosswalk)
    crosswalk = load_synonyms(a.crosswalk) if a.crosswalk else {}

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
    aliases = {}   # display-name genus token -> file slug (COL renames)
    all_slugs = {slugify(g) for g in genera}   # PR #11 review: alias keys must not collide
    # PR #68 review: a renamed entry whose display genus token IS a real genus
    # (Boletus luridiformis var. luridiformis -> neoboletus, but boletus.json exists)
    # cannot alias (collision), so it is ALSO served from the display genus's file —
    # a second copy keyed by the same taxon identity, so the site finds it either way.
    relocated = {}
    for genus in sorted(genera):
        home = slugify(genus)
        for e in genera[genus]:
            tok = slugify(e["name"].split(" ")[0])
            if tok and tok != home and tok in all_slugs:
                relocated.setdefault(tok, []).append(e)
    for genus in sorted(genera):
        slug = slugify(genus)
        rows = sorted(genera[genus], key=lambda e: e["name"]) + sorted(
            relocated.get(slug, []), key=lambda e: e["name"])
        doc = {"schema_version": 1, "genus": genus, "species": rows}
        raw = (canonical(doc) + "\n").encode("utf-8")
        path = os.path.join(a.out, f"{slug}.json")
        write_stable(path, raw)
        files[slug] = {"sha256": hashlib.sha256(raw).hexdigest(),
                       "species": len(doc["species"]), "bytes": len(raw)}
        for e in genera[genus]:
            # PR #65 review: a COL rename keeps the old genus token in the display
            # name while the file lives under the accepted genus — alias the token.
            # PR #11 review: a real genus with that slug outranks the alias (the
            # validator rejects collisions); the renamed row then degrades to the
            # plain link only if its name misses in that genus's file too.
            token = slugify(e["name"].split(" ")[0])
            if token and token != slug and token not in aliases and token not in all_slugs:
                aliases[token] = slug
    for name in (os.listdir(a.out) if os.path.isdir(a.out) else []):  # lost every species
        if name.endswith(".json") and name[:-5] not in files \
                and name not in ("index.json", "summary.json", "names.json", "squares.json"):
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
    # Review follow-up (2026-11-24): the same display name can exist under two
    # identities (a COL-lumped row plus an unmapped legacy row, or two backbone
    # homonyms) — 65 real cases. The summary renders by name, so one row per name:
    # areas are the UNION of the contributors' area sets, records and markers sum,
    # and the link keys come from the largest contributor (the details row still
    # resolves per-identity through the genus files).
    by_name = {}
    for spk, e in entries.items():
        row = by_name.setdefault(e["name"], {"areas": set(), "recs": 0, "mhlw": False,
                                             "end": False, "best": None, "all": []})
        row["all"].append((spk, e))
        row["areas"] |= set(e["area_ids"] or ())
        row["recs"] += sum(e["records_by_tier"].values())
        row["mhlw"] = row["mhlw"] or bool(e["external"]["mhlw_url"])
        row["end"] = row["end"] or e["external"]["endemic_hosoya2016"]
        rank = (len(e["area_ids"]), sum(e["records_by_tier"].values()))
        if row["best"] is None or rank > row["best"][0]:
            row["best"] = (rank, spk, e)
    ranked = sorted(by_name.items(), key=lambda kv: (-len(kv[1]["areas"]), kv[0]))
    summary = {"schema_version": 1, "snapshot": digest.hexdigest(),
               "generated": time.strftime("%Y-%m-%d"),
               "species": [[name, r["best"][2]["key"], r["best"][2]["legacy_key"], len(r["areas"]),
                            r["recs"], r["mhlw"], r["end"]] for name, r in ranked]}
    sraw = (canonical(summary) + "\n").encode("utf-8")
    write_stable(os.path.join(a.out, "summary.json"), sraw)
    # TASK-027 (decision S2): the name index — one row per summary row, same order,
    # no names repeated (they cost ~125 KiB gzip-9 on their own). A display name can
    # live under two identities: 和名 and family are the first contributor's that has
    # one, and genus_slug is the home file of the contributor whose keys the summary
    # row carries — the one genus file that always lists that entry (a relocated
    # copy may sit in the display token's file as well, PR #68). Never the alias map:
    # it keeps one target per token, and a token split over two accepted genera
    # would point half its species at a file that does not hold them.
    names_rows, syn_kept, syn_capped, syn_masked = [], 0, 0, 0
    for name, r in ranked:
        syn, capped, hid = synonyms_for(name, [e["key"] for _, e in r["all"] if e["key"]],
                                        crosswalk, gate)
        syn_kept, syn_capped, syn_masked = syn_kept + len(syn), syn_capped + capped, syn_masked + hid
        names_rows.append([next((e["name_ja"] for _, e in r["all"] if e["name_ja"]), None),
                           slugify(genus_of[r["best"][1]]),
                           next((e["family"] for _, e in r["all"] if e["family"]), None), syn])
    names = {"schema_version": 1, "snapshot": digest.hexdigest(),
             "generated": summary["generated"], "species": names_rows}
    nraw = (canonical(names) + "\n").encode("utf-8")
    write_stable(os.path.join(a.out, "names.json"), nraw)
    # TASK-028: which ~10 km square holds which species. One row per square, keyed by
    # the dashboard's MeshKey and listing the summary rows recorded there through an
    # eligible record. No name in the file: the bound "every index is a summary row"
    # is the whole gate check, since a masked species has no summary row.
    by_square, placed = {}, set()
    for i, (_, r) in enumerate(ranked):
        for spk, _ in r["all"]:
            for k in squares_of.get(spk, ()):
                by_square.setdefault(k, set()).add(i)
                placed.add(spk)
    squares = {"schema_version": 1, "snapshot": digest.hexdigest(),
               "generated": summary["generated"],
               "species": [[k, sorted(v)] for k, v in sorted(by_square.items(), key=lambda kv: tuple(map(int, kv[0].split(":"))))]}
    qraw = (canonical(squares) + "\n").encode("utf-8")
    write_stable(os.path.join(a.out, "squares.json"), qraw)
    index = {"schema_version": 1, "snapshot": digest.hexdigest(),
             "generated": time.strftime("%Y-%m-%d"), "genera": len(files),
             "species": sum(f["species"] for f in files.values()), "files": files,
             "aliases": aliases, "summary_sha256": hashlib.sha256(sraw).hexdigest(),
             "names_sha256": hashlib.sha256(nraw).hexdigest(),
             "squares_sha256": hashlib.sha256(qraw).hexdigest()}
    os.makedirs(a.out, exist_ok=True)
    with open(os.path.join(a.out, "index.json"), "w", newline="\n", encoding="utf-8") as f:
        f.write(canonical(index) + "\n")

    n = len(entries)
    ext = lambda field: sum(1 for e in entries.values()  # noqa: E731
                            if e["external"][field])
    backed = [recs for c in squares_of.values() for recs in c.values()]   # per identity-square
    print(f"{a.out}: {len(files)} genus files, {n} species (public), "
          f"{masked} masked records excluded, "
          f"{sum(f['bytes'] for f in files.values()):,} bytes genus files; "
          f"summary {len(summary['species'])} rows / {len(sraw):,} bytes; "
          f"names {len(names_rows)} rows / {len(nraw):,} bytes "
          f"(synonyms {syn_kept} kept, {syn_capped} capped, {syn_masked} masked); "
          f"squares {len(by_square)} squares / {sum(len(v) for v in by_square.values())} pairs / "
          f"{len(placed)} identities ({backed.count(1)} of {len(backed)} on one record) / "
          f"{len(qraw):,} bytes; "
          f"photo {ext('photo')}, mhlw {ext('mhlw_url')}, endemic "
          f"{sum(1 for e in entries.values() if e['external']['endemic_hosoya2016'])}, "
          f"literature {ext('literature')}, mushroomexpert {ext('mushroomexpert_url')}, "
          f"east_asia {sum(1 for e in entries.values() if e['east_asia'])}")


if __name__ == "__main__":
    main()
