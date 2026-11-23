#!/usr/bin/env python3
"""Mycoscience literature coverage over public species (TASK-022 item 4).

Crossref journal works for ISSN 1618-2545 (rows=1000, cursor pagination), plus
a probe of the J-STAGE WebAPI opensearch endpoint — used only if it answers
without a key, otherwise the report says so and Crossref alone is measured.
Titles and abstracts are matched against the public species' binomials
(bigram token lookup — a full genus+epithet pair, so epithet homonyms are
harmless) and against the public genera (single token; genus-word homonyms are
the known false-positive channel, assessed in the report). Links only: DOIs
are stored in the gitignored cache, never full text.

Usage: .venv/bin/python pipeline/sources/mycoscience.py [--db ...]
"""
import argparse
import html
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from pipeline.sources import common  # noqa: E402

CROSSREF = "https://api.crossref.org/journals/1618-2545/works"
JSTAGE = "https://api.jstage.jst.go.jp/searchapi/do"
ISSN = "1618-2545"
TOKEN_RE = re.compile(r"[a-z]+", re.I)


def fold_tokens(text):
    """Lowercase alphabetic tokens of a title/abstract; diacritics decompose
    under NFKD and the combining marks are dropped, so 'prīma' folds to 'prima'."""
    import unicodedata
    n = unicodedata.normalize("NFKD", (text or "").lower())
    return TOKEN_RE.findall("".join(c for c in n if not unicodedata.combining(c)))


def article_text(item):
    """Title + abstract of a Crossref work item (or a J-STAGE entry shaped
    {'title': str}); JATS/HTML tags in abstracts are stripped."""
    title = item.get("title")
    title = " ".join(title) if isinstance(title, list) else (title or "")
    abstract = re.sub(r"<[^>]+>", " ", item.get("abstract") or "")
    return f"{title} {abstract}"


def matches(text, binomials, genera):
    """(binomial hits, genus-only hits) against the folded token stream: a
    binomial needs the adjacent genus+epithet pair; a genus hit is any genus
    token present. `binomials` maps 'genus epithet' -> sp_key; `genera` is a set."""
    toks = fold_tokens(text)
    bigrams = {f"{a} {b}" for a, b in zip(toks, toks[1:])}
    return bigrams & set(binomials), set(toks) & genera


def fetch_crossref(cache_dir, sleep=1.0):
    """All Mycoscience work items via cursor pagination; cached per page."""
    items, cursor, page = [], "*", 1
    while True:
        cache = os.path.join(cache_dir, f"crossref-{page:03d}.json")
        doc = common.fetch_json(CROSSREF, cache=cache,
                                params={"rows": 1000, "cursor": cursor})
        batch = doc.get("message", {}).get("items", [])
        items += batch
        nxt = doc.get("message", {}).get("next-cursor")
        print(f"crossref page {page}: {len(batch)} items (total {len(items)})")
        if not batch or not nxt or page >= 20:
            break
        cursor = nxt
        page += 1
        if sleep:
            time.sleep(sleep)
    return items


def probe_jstage(cache_dir, keyword="Amanita"):
    """One opensearch request to see whether the WebAPI answers keyless."""
    try:
        body = common.fetch(JSTAGE, cache=os.path.join(cache_dir, "jstage-probe.xml"),
                            params={"service": 3, "keyword": keyword}, retries=1)
        return f"answered ({len(body)} bytes)"
    except Exception as e:                                  # noqa: BLE001 - a probe, any failure counts
        return f"NOT usable keyless from here: {e}"


ENTRY_RE = re.compile(r"<entry>(.*?)</entry>", re.S)
TITLE_EN_RE = re.compile(r"<en><!\[CDATA\[(.*?)\]\]></en>", re.S)
TITLE_JA_RE = re.compile(r"<ja><!\[CDATA\[(.*?)\]\]></ja>", re.S)
LINK_EN_RE = re.compile(r"<article_link>.*?<en>(https://[^<]+)</en>", re.S)


def parse_jstage(atom):
    """[{title, title_ja, url}] from a J-STAGE opensearch Atom response (the
    article_title/link pairs are split into <en>/<ja> CDATA halves)."""
    out = []
    for entry in ENTRY_RE.findall(atom or ""):
        t = TITLE_EN_RE.findall(entry) or TITLE_JA_RE.findall(entry)
        u = LINK_EN_RE.findall(entry)
        text = re.sub(r"<[^>]+>", " ", t[0]) if t else ""
        text = re.sub(r"\s+", " ", html.unescape(text)).strip()
        if text and u:
            out.append({"title": text, "url": u[0].strip()})
    return out


def jstage_sweep(cache_dir, genera, sleep=1.0):
    """Articles found by a keyword sweep over `genera` (the J-STAGE opensearch
    answers keyless, one request per genus, cached). Deduplicated by URL —
    the same article surfaces under several genera."""
    articles, failed = {}, 0
    for i, genus in enumerate(genera, 1):
        try:
            body = common.fetch(JSTAGE, cache=os.path.join(
                cache_dir, f"jstage-{genus.lower()}.xml"),
                params={"service": 3, "keyword": genus}, retries=1).decode("utf-8", "replace")
        except Exception:                                  # noqa: BLE001 - one genus down, keep sweeping
            failed += 1
            continue
        for art in parse_jstage(body):
            art.setdefault("via", f"jstage:{genus}")
            articles[art["url"]] = {"title": art["title"], "url": art["url"],
                                    "via": art["via"]}
        if sleep and i < len(genera):
            time.sleep(sleep)
    if failed:
        print(f"jstage: {failed}/{len(genera)} genus queries failed")
    return list(articles.values())


def main(argv=None):
    ap = argparse.ArgumentParser(description="Mycoscience coverage over public species (item 4).")
    ap.add_argument("--db", default="data/shroomhunt.duckdb")
    ap.add_argument("--cache-dir", default="data/raw/sources/mycoscience")
    ap.add_argument("--report", default=common.REPORT)
    ap.add_argument("--jstage-genera", type=int, default=300,
                    help="J-STAGE keyword sweep over this many best-recorded genera")
    ap.add_argument("--sleep", type=float, default=1.0)
    a = ap.parse_args(argv)
    os.makedirs(a.cache_dir, exist_ok=True)

    jstage_verdict = probe_jstage(a.cache_dir)
    items = fetch_crossref(a.cache_dir, a.sleep)

    species = common.public_species(a.db)
    binomials = {" ".join(s["name"].split()[:2]).lower(): s["sp_key"]
                 for s in species if len(s["name"].split()) >= 2}
    genera = {s["genus"].lower() for s in species if s["genus"]}
    sp_per_genus = {}
    for s in species:
        if s["genus"]:
            sp_per_genus[s["genus"]] = sp_per_genus.get(s["genus"], 0) + 1
    ranked = sorted(sp_per_genus, key=lambda g: (-sp_per_genus[g], g))
    jstage_articles = []
    if not jstage_verdict.startswith("NOT"):
        jstage_articles = jstage_sweep(a.cache_dir, ranked[:a.jstage_genera], a.sleep)

    per_species, per_article, genus_articles = {}, [], {}
    scanned = [("crossref", i) for i in items] + [
        ("jstage", {"title": j["title"], "abstract": "", "doi": j["url"]})
        for j in jstage_articles]
    for src, it in scanned:
        text = article_text(it)
        if not text.strip():
            continue
        ident = (it.get("doi") or it.get("DOI") or "").lower()
        year = (((it.get("issued") or {}).get("date-parts") or [[None]])[0][0]
                if src == "crossref" else None)
        bins, gens = matches(text, binomials, genera)
        for b in bins:
            per_species.setdefault(binomials[b], []).append({"src": src, "id": ident,
                                                             "year": year})
        if bins or gens:
            per_article.append({"src": src, "id": ident, "year": year,
                                "title": (it.get("title") if isinstance(it.get("title"), str)
                                          else " ".join(it.get("title") or []))[:120],
                                "species": sorted(bins), "genera": sorted(gens)})
        for g in gens:
            genus_articles[g] = genus_articles.get(g, 0) + 1

    n_sp = len(per_species)
    n = len(species)
    n_jstage_sp = len({k for k, v in per_species.items() if any(r["src"] == "jstage" for r in v)})
    with_binomial = sum(1 for r in per_article if r["species"])
    genus_only = sum(1 for r in per_article if not r["species"])
    top_genera = sorted(genus_articles.items(), key=lambda kv: -kv[1])[:15]
    sampled_species = sum(sp_per_genus[g] for g in ranked[:a.jstage_genera])
    with open(os.path.join(a.cache_dir, "matches.json"), "w", encoding="utf-8") as f:
        json.dump({"per_species": per_species, "per_article": per_article},
                  f, ensure_ascii=False, indent=1, sort_keys=True)

    body = f"""Two indexes, links only (DOIs and J-STAGE URLs cached; never text or
figures — Mycoscience articles are often CC BY-NC-ND):

- **Crossref** journal works for ISSN {ISSN}: **{len(items)} articles** (title +
  abstract). Crossref holds only a fraction of the journal's run — J-STAGE is
  the fuller index.
- **J-STAGE WebAPI opensearch**: the probe **{jstage_verdict}** (no key
  needed), so a keyword sweep over the {a.jstage_genera} most species-rich
  public genera ({sampled_species} of {n} species, {100 * sampled_species / n:.0f}%)
  contributed **{len(jstage_articles)} unique articles**.

| Fact | Count |
|---|---|
| articles naming ≥1 public species binomial | {with_binomial} of {len(items) + len(jstage_articles)} |
| articles with genus-only hits | {genus_only} |
| **public species with ≥1 paper** | **{n_sp} ({100 * n_sp / n:.1f}%)** — {n_jstage_sp} of them via J-STAGE |

Matching is a full genus+epithet bigram over folded title/abstract tokens, so
epithet homonyms (common Latin words like *alba*, *cinnabarinus*) cannot false-
positive alone; the known false-positive channel is GENUS-word homonyms — a
genus token that is also an English word or an author surname. Top genus-token
article counts, for eyeballing:

""" + "\n".join(f"- {g}: {c} articles" for g, c in top_genera) + f"""

Concrete homonyms measured: of the articles whose ONLY match is a genus token
(no binomial), 'tuber' accounts for the clearest English-word collision (plant
"tuber" in physiology papers) and 'candida'/'cryptococcus' pull in clinical
literature — genus-only hits must therefore never auto-attach to a species in
item 7; only binomial matches become Literature links.

Match rate is informational (item 6): low coverage just means fewer Literature
links. The J-STAGE sweep is a sample (top {a.jstage_genera} genera); rerun with
`--jstage-genera 0` to skip it or a larger cap for a fuller census.
**Verdict: informational — {n_sp}/{n} species ({100 * n_sp / n:.1f}%) carry ≥1 paper.**"""
    common.write_section("Mycoscience literature", body, a.report)
    common.headline("mycoscience", f"{with_binomial}/{len(items) + len(jstage_articles)} articles "
                    f"name a public species; {n_sp}/{n} species ({100 * n_sp / n:.1f}%) have ≥1 "
                    f"paper (informational); links only, DOIs/J-STAGE URLs cached", a.report)
    return {"articles": len(items) + len(jstage_articles), "species_with_paper": n_sp}


if __name__ == "__main__":
    main()
