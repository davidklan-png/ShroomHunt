#!/usr/bin/env python3
"""Join the reviewed species-source outputs into the enrichment table (TASK-024,
spec item 7).

One row per PUBLIC sp_key in data/reference/enrichment.csv, with a retrieval-date
sidecar next to it. Inputs, all produced by the reviewed TASK-022/023 runs:

  Wikidata   data/raw/sources/wikidata/species_wikidata.json (per sp_key:
             qid / sitelinks / first licence-acceptable P18 file, from
             pipeline/sources/wikidata.py) plus the cached Commons
             imageinfo responses for author + licence URL;
  MHLW       data/reference/mhlw_toxic.csv (sp_key -> risk-profile URL);
  Hosoya     data/reference/hosoya_endemic_2016.csv (sp_key -> endemic);
  Mycoscience data/raw/sources/mycoscience/matches.json (per-article DOI/title/
             year and the binomials each names — only binomial matches attach,
             genus-only hits never do);
  MushroomExpert data/reference/mushroomexpert_links.csv (sp_key, level, url).

NEVER blocks publish (spec item 7 binding): a missing cache or reference CSV
leaves that source's fields empty for NEW species and keeps the previously
retrieved values for known ones (the committed enrichment.csv stays the last
full retrieval; the sidecar dates it), with a warning on stderr. Photos are
Commons files whose licence classifies CC0 / CC BY / CC BY-SA with author and
licence URL present (spec section 2); thumbnail URLs follow the Commons
thumb.php pattern verified against the cached file metadata (f=<file>&w=96,
underscores in file-page titles).

Usage: python3 pipeline/enrich.py [--db data/shroomhunt.duckdb]
         [--out data/reference/enrichment.csv] [--sidecar ...]
         [--wikidata-cache data/raw/sources/wikidata] [--mycoscience-cache ...]
         [--mhlw ...] [--hosoya ...] [--mushroomexpert ...]  [gate inputs]
"""
import argparse, csv, html, io, json, os, re, sys, time, urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from public_taxa import load_gate  # noqa: E402
from col_xr import ensure, exprs, keyed  # noqa: E402
from sources.wikidata import ACCEPTABLE, classify_licence  # noqa: E402

COMMONS = "https://commons.wikimedia.org"
WIKIDATA_COLS = ("wikidata_qid", "wikipedia_ja", "wikipedia_en")
PHOTO_COLS = ("photo_thumb_url", "photo_file_page", "photo_author",
              "photo_licence", "photo_licence_url")
COLUMNS = ("sp_key", "name", *WIKIDATA_COLS, *PHOTO_COLS, "mhlw_url",
           "endemic_hosoya2016", "literature", "mushroomexpert_url", "mushroomexpert_level")


def public_species(con, gate):
    """[(sp_key, name)] — the gated species list, exactly as the sources scripts
    and the bundle builder count them (col_xr identity, gate in SQL)."""
    jp_cols = {r[0] for r in con.execute(
        "select column_name from information_schema.columns where table_name='jp'").fetchall()}
    X = exprs("jp", jp_cols)
    gate.install_sql(con)
    return con.execute(f"""select {X['sp_key']}, {X['sp_name']} from jp
                           where {X['sp_key']} is not null and {gate.sql_where("jp")}
                           group by 1,2""").fetchall()


def read_spkey_csv(path, key_col="sp_key"):
    """{sp_key: row} (or None when missing) from a comment-prefixed reference CSV
    whose key column is sp_key (MushroomExpert) or col_key (MHLW, Hosoya) — both
    hold the public species key the sources matched to."""
    if not os.path.exists(path):
        return None
    with open(path, newline="", encoding="utf-8") as f:
        lines = [ln for ln in f if not ln.lstrip().startswith("#")]
    return {r[key_col]: r for r in csv.DictReader(lines)}


def load_json(path):
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except ValueError as e:
        print(f"enrich: {path} does not parse ({e}); treating as missing", file=sys.stderr)
        return None


def clean_author(html_text):
    """Author credit as plain text: tags stripped, entities decoded, whitespace folded."""
    text = html.unescape(re.sub(r"<[^>]+>", " ", html_text or ""))
    return " ".join(text.split())


def load_commons_meta(cache_dir):
    """{'File:<title>': {author, licence, licence_url}} from the cached Commons
    imageinfo responses (action=query&prop=imageinfo&iiprop=extmetadata)."""
    meta = {}
    for name in sorted(os.listdir(cache_dir)):
        if not (name.startswith("commons-imageinfo-") and name.endswith(".json")):
            continue
        doc = load_json(os.path.join(cache_dir, name)) or {}
        for page in doc.get("query", {}).get("pages", {}).values():
            title = page.get("title")
            em = ((page.get("imageinfo") or [{}])[0].get("extmetadata")) or {}
            val = lambda k: ((em.get(k) or {}).get("value") or "").strip()  # noqa: E731
            if title:
                meta[title] = {"author": clean_author(val("Artist")),
                               "licence": val("LicenseShortName"),
                               "licence_url": val("LicenseUrl")}
    return meta


def wiki_url(title, lang):
    if not title:
        return ""
    return f"https://{lang}.wikipedia.org/wiki/" + urllib.parse.quote(
        title.replace(" ", "_"), safe="")


def photo_for(wd, commons):
    """The species' photo dict, or None: a P18 file whose cached Commons metadata
    carries an acceptable licence class AND author AND licence URL (spec section 2)."""
    if not wd or not wd.get("p18"):
        return None
    m = (commons or {}).get("File:" + wd["p18"])
    if not m or not m["author"] or not m["licence_url"]:
        return None
    if classify_licence(m["licence"], m["licence_url"]) not in ACCEPTABLE:
        return None
    f = urllib.parse.quote(wd["p18"], safe="")
    return {"thumb_url": f"{COMMONS}/w/thumb.php?f={f}&w=96",
            "file_page": f"{COMMONS}/wiki/File:" + urllib.parse.quote(
                wd["p18"].replace(" ", "_"), safe=""),
            "author": m["author"], "licence": m["licence"], "licence_url": m["licence_url"]}


def literature_for(cache_dir, by_binomial):
    """{sp_key: [top 5 {doi, title, year}, newest first]} from matches.json —
    only binomial matches attach to a species (genus-only hits never do)."""
    doc = load_json(os.path.join(cache_dir, "matches.json"))
    if doc is None:
        return None
    per = {}
    for art in doc.get("per_article", []):
        row = {"doi": (art.get("id") or "").strip(),
               "title": " ".join((art.get("title") or "").split()),
               "year": art.get("year")}
        if not row["doi"] or not row["title"]:
            continue
        for binomial in art.get("species", []):
            k = by_binomial.get(binomial)
            if k:
                per.setdefault(k, {}).setdefault(row["doi"], row)
    return {k: sorted(v.values(), key=lambda r: (-(r["year"] or 0), r["doi"]))[:5]
            for k, v in per.items()}


def write_output(a, rows):
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=COLUMNS, lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    text = ("# Species enrichment joined from the reviewed species sources (TASK-024,\n"
            "# spec item 7): Wikidata/Commons caches (pipeline/sources/wikidata.py),\n"
            "# MHLW (mhlw_toxic.csv), Hosoya et al. 2016 (hosoya_endemic_2016.csv),\n"
            "# Mycoscience caches (pipeline/sources/mycoscience.py), MushroomExpert\n"
            "# (mushroomexpert_links.csv). PUBLIC species only; masked species never\n"
            "# appear. Photos are Commons files licence-classed CC0 / CC BY / CC BY-SA\n"
            "# with author and licence URL; every external item is link-only (spec\n"
            "# section 2). Empty field = none. Regenerated by pipeline/enrich.py; the\n"
            "# retrieval date lives in the sidecar next to this file.\n") + buf.getvalue()
    unchanged = os.path.exists(a.out) and open(a.out, encoding="utf-8").read() == text
    if not unchanged:
        with open(a.out, "w", newline="", encoding="utf-8") as f:
            f.write(text)
        # the sidecar keeps its original date until the content actually moves, so the
        # workflow's cache key (hashFiles of the sidecar) stays stable across no-op runs
        with open(a.sidecar, "w", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d") + "\n")
    return unchanged


def main(argv=None):
    ap = argparse.ArgumentParser(description="Join the species-source outputs into "
                                             "data/reference/enrichment.csv (spec item 7).")
    ap.add_argument("--db", default="data/shroomhunt.duckdb")
    ap.add_argument("--out", default="data/reference/enrichment.csv")
    ap.add_argument("--sidecar", default="data/reference/enrichment-retrieved.txt")
    ap.add_argument("--wikidata-cache", default="data/raw/sources/wikidata")
    ap.add_argument("--mycoscience-cache", default="data/raw/sources/mycoscience")
    ap.add_argument("--mhlw", default="data/reference/mhlw_toxic.csv")
    ap.add_argument("--hosoya", default="data/reference/hosoya_endemic_2016.csv")
    ap.add_argument("--mushroomexpert", default="data/reference/mushroomexpert_links.csv")
    ap.add_argument("--mask", default="data/reference/taxon_mask.csv")
    ap.add_argument("--coverage", default="data/reference/redlist_coverage.csv")
    ap.add_argument("--exceptions", default="data/reference/match_exceptions.csv")
    ap.add_argument("--redlist", default="data/raw/redlist/redlist_fungi_jp.json")
    ap.add_argument("--col-mask-map", default="data/reference/col_mask_map.csv")
    a = ap.parse_args(argv)

    import duckdb
    con = duckdb.connect(a.db)
    try:
        ensure(con)
        gate = load_gate(mask=a.mask, coverage=a.coverage, exceptions=a.exceptions,
                         redlist=a.redlist, col_mask=a.col_mask_map if keyed(con) else None)
        species = public_species(con, gate)
    finally:
        con.close()

    wd_rows = load_json(os.path.join(a.wikidata_cache, "species_wikidata.json"))
    commons = load_commons_meta(a.wikidata_cache) if wd_rows is not None else None
    mhlw = read_spkey_csv(a.mhlw, "col_key")
    hosoya = read_spkey_csv(a.hosoya, "col_key")
    me = read_spkey_csv(a.mushroomexpert, "sp_key")
    # sorted: when two sp_keys share a binomial (a COL lump plus an unmapped legacy
    # row), the lowest sp_key wins the papers — deterministic across runs, since the
    # GROUP BY behind `species` returns rows in no guaranteed order
    by_binomial = {" ".join(n.split()[:2]).lower(): k
                   for k, n in sorted(species) if len(n.split()) >= 2}
    lit = literature_for(a.mycoscience_cache, by_binomial)
    for what, path in (("wikidata cache", a.wikidata_cache), ("mycoscience cache", a.mycoscience_cache),
                       ("MHLW csv", a.mhlw), ("Hosoya csv", a.hosoya),
                       ("MushroomExpert csv", a.mushroomexpert)):
        if not os.path.exists(path):
            print(f"enrich: {what} missing ({path}); those fields keep their last "
                  f"retrieved values or stay empty — never blocks publish", file=sys.stderr)
    if commons is not None and not commons:
        print("enrich: no cached Commons imageinfo responses; photo fields keep their "
              "last retrieved values or stay empty", file=sys.stderr)

    old = read_spkey_csv(a.out) or {}

    def keep(cols, sp_key):
        """Fallback: the last retrieved values for one source group."""
        row = old.get(sp_key) or {}
        return {c: (row.get(c) or "") for c in cols}

    out_rows = []
    for sp_key, name in sorted(species):
        wd = (wd_rows or {}).get(sp_key)
        row = {"sp_key": sp_key, "name": name}
        if wd_rows is None:
            row.update(keep(WIKIDATA_COLS, sp_key))
        else:
            row.update({"wikidata_qid": (wd or {}).get("qid") or "",
                        "wikipedia_ja": wiki_url((wd or {}).get("jawp"), "ja"),
                        "wikipedia_en": wiki_url((wd or {}).get("enwp"), "en")})
        if wd_rows is None or commons is None:
            row.update(keep(PHOTO_COLS, sp_key))
        else:
            photo = photo_for(wd, commons) or {}
            row.update({"photo_thumb_url": photo.get("thumb_url", ""),
                        "photo_file_page": photo.get("file_page", ""),
                        "photo_author": photo.get("author", ""),
                        "photo_licence": photo.get("licence", ""),
                        "photo_licence_url": photo.get("licence_url", "")})
        row.update({"mhlw_url": ((mhlw.get(sp_key) or {}).get("url") or "")}
                   if mhlw is not None else {"mhlw_url": keep(("mhlw_url",), sp_key)["mhlw_url"]})
        row.update({"endemic_hosoya2016": "1" if sp_key in hosoya else ""}
                   if hosoya is not None else
                   {"endemic_hosoya2016": keep(("endemic_hosoya2016",), sp_key)["endemic_hosoya2016"]})
        row.update({"literature": json.dumps(lit[sp_key], ensure_ascii=False,
                                             separators=(",", ":")) if lit.get(sp_key) else ""}
                   if lit is not None else
                   {"literature": keep(("literature",), sp_key)["literature"]})
        if me is None:
            row.update(keep(("mushroomexpert_url", "mushroomexpert_level"), sp_key))
        else:
            m = me.get(sp_key) or {}
            row.update({"mushroomexpert_url": m.get("url") or "",
                        "mushroomexpert_level": m.get("level") or ""})
        out_rows.append(row)

    write_output(a, out_rows)
    n = len(out_rows)
    full = lambda f: sum(1 for r in out_rows if r[f])  # noqa: E731
    print(f"{a.out}: {n} rows (public species), retrieval sidecar {a.sidecar}; "
          f"wikidata items {full('wikidata_qid')}, ja {full('wikipedia_ja')}, en {full('wikipedia_en')}, "
          f"photos {full('photo_thumb_url')}, mhlw {full('mhlw_url')}, endemic {full('endemic_hosoya2016')}, "
          f"literature {full('literature')}, mushroomexpert {full('mushroomexpert_url')}")


if __name__ == "__main__":
    main()
