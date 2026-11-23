#!/usr/bin/env python3
"""MushroomExpert coverage over public genera and species (TASK-022 item 5).

Fetches https://www.mushroomexpert.com/major_groups.html once with a
descriptive UA and caches the HTML under data/raw/sources/mushroomexpert/ (gitignored; the site is
all-rights-reserved, its pages must not be committed) and the extracted link
list into data/reference/mushroomexpert_links.csv, with a
retrieval-date sidecar; follows the index's own same-host links ONE level
(meta-refresh redirects included), and matches our public species and genera
by link text. No guessed URLs, nothing beyond what the pages list; all
rights reserved means link-only, labelled "North America".

Usage: .venv/bin/python pipeline/sources/mushroomexpert.py [--db ...]
        [--cache-dir data/raw/sources/mushroomexpert] [--sleep 1.0]
"""
import argparse
import html
import os
import re
import sys
import time
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from pipeline.sources import common  # noqa: E402

INDEX = "https://www.mushroomexpert.com/major_groups.html"
LINK_RE = re.compile(r'<a\s+[^>]*href=(["\'])([^"\']+)\1[^>]*>(.*?)</a>', re.S | re.I)
META_REFRESH_RE = re.compile(r'url=([^">]+)', re.I)
STOP_FLOOR = 20            # spec item 6: genus coverage below ~20% is a stop


def page_links(page, base):
    """[(url, text)] for every <a> on a page, resolved against `base`, tags
    stripped, whitespace collapsed. Same-host http(s) only."""
    out = []
    for _, href, label in LINK_RE.findall(page):
        url = urllib.parse.urljoin(base, html.unescape(href))
        if urllib.parse.urlparse(url).netloc.lower().endswith("mushroomexpert.com"):
            text = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", label))).strip()
            if text:
                out.append((url, text))
    return out


def meta_refresh(page, base):
    """The URL a <meta HTTP-EQUIV="Refresh" CONTENT="0;URL=…"> redirect points
    at (MushroomExpert's agaricales.html → gilled.html), or None."""
    if re.search(r'http-equiv\s*=\s*["\']?refresh', page or "", re.I):
        m = META_REFRESH_RE.search(page)
        if m:
            return urllib.parse.urljoin(base, html.unescape(m.group(1).strip("'\"")))
    return None


def follow(url, cache_path, retries=common.RETRIES):
    """Fetch a page, transparently following one meta-refresh hop (cache both)."""
    page = common.fetch(url, cache=cache_path + ".html", retries=retries).decode(
        "utf-8", "replace")
    nxt = meta_refresh(page, url)
    if nxt and nxt != url:
        page = common.fetch(nxt, cache=cache_path + "__redirect.html",
                            retries=retries).decode("utf-8", "replace")
        url = nxt
    return url, page


def extract_links(page_html, species):
    """(species_hits, genus_hits) of MushroomExpert link texts matched to our
    public taxa: a species needs the full binomial as the link text (case- and
    punctuation-folded); a genus needs the bare genus token."""
    wanted_sp = {}
    for s in species:
        parts = s["name"].split()
        if len(parts) >= 2:
            wanted_sp[f"{parts[0].lower()} {parts[1].lower()}"] = s["sp_key"]
    wanted_g = {s["genus"].lower(): s["genus"] for s in species if s["genus"]}
    def fold(t):
        import unicodedata
        n = unicodedata.normalize("NFKD", t.lower())
        n = "".join(c for c in n if not unicodedata.combining(c))
        return re.sub(r"[^a-z ]", " ", n).strip()

    sp, gen = {}, {}
    for url, text in page_links(page_html, INDEX):
        f = fold(text)
        if " " not in f and f in wanted_g:
            gen.setdefault(f, url)
        elif f in wanted_sp:
            sp.setdefault(wanted_sp[f], url)
    return sp, gen


def main(argv=None):
    ap = argparse.ArgumentParser(description="MushroomExpert coverage over public taxa (item 5).")
    ap.add_argument("--db", default="data/shroomhunt.duckdb")
    ap.add_argument("--cache-dir", default="data/raw/sources/mushroomexpert")
    ap.add_argument("--report", default=common.REPORT)
    ap.add_argument("--sleep", type=float, default=1.0)
    a = ap.parse_args(argv)
    os.makedirs(a.cache_dir, exist_ok=True)

    sidecar = os.path.join(a.cache_dir, "mushroomexpert-retrieved.txt")
    index_cache = os.path.join(a.cache_dir, "mushroomexpert-major_groups")
    fresh = not os.path.exists(index_cache + ".html")
    url, index_html = follow(INDEX, index_cache)
    retrieved = time.strftime("%Y-%m-%d") if fresh else (
        open(sidecar).read().strip() if os.path.exists(sidecar) else "unknown")
    open(sidecar, "w").write(retrieved + "\n")

    level1 = {}
    for u, text in page_links(index_html, INDEX):
        level1.setdefault(u.split("#")[0].rstrip("/"), text)
    skip_prefix = ("glossary", "about", "contributors", "studying")
    pages = []
    for i, (u, text) in enumerate(sorted(level1.items()), 1):
        if any(u.rsplit("/", 1)[-1].startswith(p) for p in skip_prefix):
            continue
        final, page = follow(u, os.path.join(
            a.cache_dir, "mushroomexpert-" + u.rsplit("/", 1)[-1].replace(".html", "")))
        pages.append(page)
        if a.sleep and i < len(level1):
            time.sleep(a.sleep)

    # Level 2 (TASK-023, David-approved 2026-11-23): genus pages sit behind the
    # group key pages, so follow the on-site links each level-1 page lists.
    # Still only URLs the site's own pages list; never guessed.
    seen = {u for u in level1}
    level2 = []
    for page in pages:
        for u, _t in page_links(page, INDEX):
            u = u.split("#")[0].rstrip("/")
            if u in seen or not u.endswith(".html"):
                continue
            if any(u.rsplit("/", 1)[-1].startswith(pfx) for p in (skip_prefix,) for pfx in p):
                continue
            seen.add(u)
            level2.append(u)

    species = common.public_species(a.db)
    sp_hits, gen_hits = extract_links(index_html, species)
    for page in pages:
        s, g = extract_links(page, species)
        sp_hits.update(s)
        gen_hits.update(g)
    for i, u in enumerate(sorted(level2), 1):
        slug = re.sub(r"[^a-z0-9]+", "-", u.lower()).strip("-")[-100:]
        _final, page = follow(u, os.path.join(a.cache_dir, "l2-" + slug))
        s, g = extract_links(page, species)
        sp_hits.update(s)
        gen_hits.update(g)
        if a.sleep and i % 25 == 0:
            time.sleep(a.sleep * 5)   # breathe every 25 pages
        elif a.sleep:
            time.sleep(a.sleep)

    n_sp, n_gen = len(species), len({s["genus"] for s in species if s["genus"]})
    gen_pct = 100 * len(gen_hits) / n_gen if n_gen else 0
    with open(os.path.join(a.cache_dir, "..", "..", "..", "reference", "mushroomexpert_links.csv"), "w",
              encoding="utf-8") as f:
        f.write("# MushroomExpert link targets for PUBLIC ShroomHunt taxa (link-only source,\n")
        f.write(f"# all rights reserved); index retrieved {retrieved}.\n")
        f.write("sp_key,level,url\n")
        for k, u in sorted(sp_hits.items()):
            f.write(f"{k},species,{u}\n")
        for g, u in sorted(gen_hits.items()):
            f.write(f"{g},genus,{u}\n")

    body = f"""Index `{INDEX}` (cached under `{a.cache_dir}/`, retrieved {retrieved}, with a
retrieval-date sidecar) plus its own links followed two levels ({len(pages)} + {len(level2)} pages;
meta-refresh hops like agaricales.html → gilled.html followed transparently).
No URLs are guessed and nothing beyond what those pages list is probed.

| Taxon level | Public taxa | With a MushroomExpert page | Coverage |
|---|---|---|---|
| species (full binomial link text) | {n_sp} | {len(sp_hits)} | {100 * len(sp_hits) / n_sp:.1f}% |
| genus (bare genus link text) | {n_gen} | {len(gen_hits)} | **{gen_pct:.1f}%** |

Matching is on link TEXT folded to letters and spaces: a species needs its
full binomial as the link text; a genus its bare name. MushroomExpert is all
rights reserved and North America–centric: link-only, labelled "North America"
(spec §2). Some genus-text links resolve to family/group pages (e.g. the
"Cantharellus" text points at cantharellaceae.html) — visible in the links
CSV, to be resolved at item-7 time. Genus coverage floor {STOP_FLOOR}%.
**Verdict: genus coverage {gen_pct:.1f}% — {'ABOVE' if gen_pct >= STOP_FLOOR else 'BELOW'} the floor.**
Link targets for review: `data/reference/mushroomexpert_links.csv`.

Two-level crawl (TASK-023): still {gen_pct:.1f}% genus coverage — the floras genuinely barely overlap; accept-or-drop is David's call, not another level.
"""
    common.write_section("MushroomExpert (North America, link-only)", body, a.report)
    common.headline("mushroomexpert", f"{len(gen_hits)}/{n_gen} public genera ({gen_pct:.1f}%, "
                    f"floor {STOP_FLOOR}%: {'PASS' if gen_pct >= STOP_FLOOR else 'STOP'}), "
                    f"{len(sp_hits)} species pages; link-only", a.report)
    return {"genera": n_gen, "genera_hit": len(gen_hits), "species_hit": len(sp_hits)}


if __name__ == "__main__":
    main()
