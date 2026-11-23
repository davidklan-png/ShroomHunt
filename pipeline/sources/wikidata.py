#!/usr/bin/env python3
"""Wikidata / Wikipedia / Commons coverage over the public species (TASK-022 item 1).

For every public species' legacy backbone key (Wikidata P846 still uses legacy
keys): does a WDQS item exist; do ja/en Wikipedia sitelinks; does P18 have an
image whose Commons extmetadata licence is one of CC0 / CC BY / CC BY-SA (spec
§2 binding: anything else or missing is NOT usable). Percentages go into the
shared report; raw SPARQL/API responses are cached under data/raw/sources/
wikidata/ so re-runs never re-hit the APIs.

Usage: .venv/bin/python pipeline/sources/wikidata.py [--db data/shroomhunt.duckdb]
        [--cache-dir data/raw/sources/wikidata] [--chunk 500] [--sleep 1.0]
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from pipeline.sources import common  # noqa: E402

WDQS = "https://query.wikidata.org/sparql"
COMMONS_API = "https://commons.wikimedia.org/w/api.php"
ACCEPTABLE = ("CC0", "CC BY", "CC BY-SA")
STOP_FLOOR = 20            # spec item 6: Wikidata item coverage below ~20% is a stop condition

QUERY = """SELECT ?key ?item ?jawp ?enwp ?p18 WHERE {
  VALUES ?key { %s }
  ?item wdt:P846 ?key.
  OPTIONAL { ?ja schema:about ?item; schema:isPartOf <https://ja.wikipedia.org/>.
             BIND(STRAFTER(STR(?ja), 'https://ja.wikipedia.org/wiki/') AS ?jawp) }
  OPTIONAL { ?en schema:about ?item; schema:isPartOf <https://en.wikipedia.org/>.
             BIND(STRAFTER(STR(?en), 'https://en.wikipedia.org/wiki/') AS ?enwp) }
  OPTIONAL { ?item wdt:P18 ?p18. }
}"""


def parse_sparql(doc):
    """{legacy key: {qid, jawp, enwp, p18}} from a WDQS JSON response. P18 arrives
    as a Special:FilePath URL; the file title is its decoded last segment."""
    out = {}
    for b in doc.get("results", {}).get("bindings", []):
        g = lambda k: b[k]["value"] if k in b else None
        key = g("key")
        if key is None:
            continue
        row = out.setdefault(key, {"qid": None, "jawp": None, "enwp": None, "p18": []})
        if row["qid"] is None and g("item"):
            row["qid"] = g("item").rsplit("/", 1)[-1]
        for f in ("jawp", "enwp"):
            if row[f] is None and g(f):
                row[f] = urllib.parse.unquote(g(f))
        if g("p18"):
            tail = g("p18").rsplit("/", 1)[-1]
            name = urllib.parse.unquote(tail) if "." in tail else None
            if name and name not in row["p18"]:        # PR #6 review: keep every P18 candidate
                row["p18"].append(name)
    return out


def classify_licence(short_name, licence_url=""):
    """'CC0' / 'CC BY' / 'CC BY-SA' when the licence is one of the three the spec
    allows, else None (public domain, NC/ND, GFDL, fair use, missing...)."""
    s = f"{short_name or ''} {licence_url or ''}".lower()
    if (re.search(r"n[co]n?commercial|(?:^|[^a-z])(?:nc|nd)(?:[^a-z]|$)", s)
            or "-nd" in s or "noderivs" in s or "-nc" in s):
        return None
    if "cc0" in s or "publicdomain/zero" in s:
        return "CC0"
    if "by-sa" in s or "by_sa" in s:
        return "CC BY-SA"
    if re.search(r"\bby\b", s) and ("creativecommons.org/licenses/by" in s
                                    or re.search(r"cc[\s-]*by", s)):
        return "CC BY"
    return None


def parse_imageinfo(doc):
    """{'File:Title.jpg': licence_class or None} from a Commons action=query prop=
    imageinfo&iiprop=extmetadata response (batched titles)."""
    out = {}
    for page in doc.get("query", {}).get("pages", {}).values():
        title = page.get("title")
        if not title:
            continue
        em = (page.get("imageinfo") or [{}])[0].get("extmetadata") or {}
        val = lambda k: em.get(k, {}).get("value", "") or ""
        out[title] = classify_licence(val("LicenseShortName"), val("LicenseUrl"))
    return out


def chunked(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def main(argv=None):
    ap = argparse.ArgumentParser(description="Wikidata/Commons coverage over public species (item 1).")
    ap.add_argument("--db", default="data/shroomhunt.duckdb")
    ap.add_argument("--cache-dir", default="data/raw/sources/wikidata")
    ap.add_argument("--report", default=common.REPORT)
    ap.add_argument("--chunk", type=int, default=500, help="legacy keys per SPARQL VALUES block")
    ap.add_argument("--sleep", type=float, default=1.0)
    a = ap.parse_args(argv)
    os.makedirs(a.cache_dir, exist_ok=True)

    species = common.public_species(a.db)
    keys = sorted({k for s in species for k in s["legacy_keys"]}, key=int)
    print(f"public species {len(species)}, {len(keys)} legacy keys -> "
          f"{-(-len(keys) // a.chunk)} SPARQL chunks of {a.chunk}")

    by_key = {}
    for i, chunk in enumerate(chunked(keys, a.chunk), 1):
        cache = os.path.join(a.cache_dir, f"sparql-{i:03d}.json")
        doc = common.fetch_json(WDQS, cache=cache, data=urllib.parse.urlencode(
            {"query": QUERY % " ".join(f'"{k}"' for k in chunk), "format": "json"}).encode(),
            headers={"Accept": "application/sparql-results+json"})
        by_key.update(parse_sparql(doc))
        if a.sleep and i * a.chunk < len(keys):
            time.sleep(a.sleep)

    titles = sorted({p for v in by_key.values() for p in v["p18"]})
    print(f"P18 image titles: {len(titles)} -> imageinfo batches of 50")
    licences = {}
    for i, batch in enumerate(chunked(titles, 50), 1):
        cache = os.path.join(a.cache_dir, f"commons-imageinfo-{i:03d}.json")
        doc = common.fetch_json(COMMONS_API, cache=cache, params={
            "action": "query", "prop": "imageinfo", "iiprop": "extmetadata",
            "format": "json", "titles": "|".join("File:" + t for t in batch)})
        licences.update(parse_imageinfo(doc))
        if a.sleep and i * 50 < len(titles):
            time.sleep(a.sleep)

    def ext(species_row):
        """The species' best Wikidata facts across all its legacy keys."""
        hits = [by_key[k] for k in species_row["legacy_keys"] if k in by_key]
        first = lambda f: next((h[f] for h in hits if h[f]), None)
        # PR #6 review: try every P18 candidate; the first licence-acceptable one wins,
        # else report the first candidate with its (unusable) licence for the stats
        cands = [p for h in hits for p in h["p18"]]
        p18, lic = None, None
        for c in cands:
            l = licences.get(f"File:{c}")
            if l in ACCEPTABLE:
                p18, lic = c, l
                break
        if p18 is None and cands:
            p18, lic = cands[0], licences.get(f"File:{cands[0]}")
        return {"item": bool(hits), "qid": first("qid"), "jawp": first("jawp"),
                "enwp": first("enwp"), "p18": p18, "licence": lic}

    rows = {s["sp_key"]: ext(s) for s in species}
    n = len(rows)
    pct = lambda c: f"{c} ({100 * c / n:.1f}%)" if n else "0 (n/a)"
    with_item = sum(1 for r in rows.values() if r["item"])
    ja = sum(1 for r in rows.values() if r["jawp"])
    en = sum(1 for r in rows.values() if r["enwp"])
    usable = sum(1 for r in rows.values() if r["licence"] in ACCEPTABLE)
    rejected = sum(1 for r in rows.values() if r["p18"] and r["licence"] not in ACCEPTABLE)
    with open(os.path.join(a.cache_dir, "species_wikidata.json"), "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=1, sort_keys=True)

    body = f"""Public species measured: **{n}** (their {len(keys)} legacy backbone keys, P846)

| Fact | Count |
|---|---|
| Wikidata item via P846 | {pct(with_item)} |
| ja.wikipedia sitelink | {pct(ja)} |
| en.wikipedia sitelink | {pct(en)} |
| either sitelink | {pct(sum(1 for r in rows.values() if r['jawp'] or r['enwp']))} |
| P18 image present | {pct(sum(1 for r in rows.values() if r['p18']))} |
| **usable image** (CC0 / CC BY / CC BY-SA) | **{pct(usable)}** |
| P18 present but licence unusable | {rejected} |

Licence policy is spec §2, binding: Commons images only with CC0 / CC BY /
CC BY-SA, read from `action=query&prop=imageinfo&iiprop=extmetadata`
(LicenseShortName + LicenseUrl); NC/ND/GFDL/public-domain or missing metadata
all count as unusable. Raw SPARQL and Commons responses are cached under
`{a.cache_dir}/` (wikidata.org and the MediaWiki APIs 403 unnamed agents, so
every request carries the ShroomHunt UA).

Stop condition (spec item 6): Wikidata item coverage must be ≥ ~{STOP_FLOOR}%.
**Verdict: {100 * with_item / n if n else 0:.1f}% — {'ABOVE' if with_item * 100 >= STOP_FLOOR * n else 'BELOW'} the floor.**
Per-species detail (public species only): `{a.cache_dir}/species_wikidata.json`."""
    common.write_section("Wikidata (P846 → sitelinks → P18 image)", body, a.report)
    common.headline("wikidata", f"{pct(with_item)} with item, {pct(usable)} with usable image "
                    f"(floor {STOP_FLOOR}%: {'PASS' if with_item * 100 >= STOP_FLOOR * n else 'STOP'})",
                    a.report)
    return {"species": n, "item": with_item, "ja": ja, "en": en, "usable_image": usable}


if __name__ == "__main__":
    main()
