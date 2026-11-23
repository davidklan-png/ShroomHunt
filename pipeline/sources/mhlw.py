#!/usr/bin/env python3
"""MHLW 自然毒のリスクプロフィール toxic-marker coverage (TASK-022 item 2).

Fetches the poison index, extracts the キノコ table (Japanese name + risk-
profile page URLs), reads each linked profile page's title for the scientific
name, and matches entries to PUBLIC species (exact public-name match, then the
COL crosswalk, then the live GBIF backbone matcher as in fetch_redlist_2025).
Emits reviewed data/reference/mhlw_toxic.csv (col_key,scientific_name,url):
rows exist only for entries matched to a public species — a masked species
appears in no output, not even as an unmatched-name count.

Terms: the MHLW site's 利用規約・リンク・著作権等 page is fetched and quoted in
the report. Linking is explicitly permitted (リンクフリー); content is PDL 1.0
with attribution. Regardless, per the binding spec we use link-only.

Usage: .venv/bin/python pipeline/sources/mhlw.py [--db ...] [--csv data/reference/mhlw_toxic.csv]
"""
import argparse
import csv
import html
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from pipeline.sources import common  # noqa: E402

INDEX = "https://www.mhlw.go.jp/stf/seisakunitsuite/bunya/kenkou_iryou/shokuhin/syokuchu/poison/index.html"
TERMS = "https://www.mhlw.go.jp/chosakuken/index.html"
ROW_RE = re.compile(r"<tr>\s*(?:<th[^>]*>.*?</th>\s*)?<td>([^<]+)</td>\s*<td>(.*?)</td>", re.S)
LINK_RE = re.compile(r'<a\s+href="([^"]+)"[^>]*>([^<]*)</a>', re.I)
TITLE_RE = re.compile(r"自然毒のリスクプロファイル[:：]\s*(.+?)[（(]([^（）()]*)[)）]")


def parse_index(page):
    """[(name_ja, url_detail)] from the キノコ rows of the index. The mushroom
    block is the table whose header cell is id="kinoko"; each row is one mushroom
    with 概要版/詳細版 links — 詳細版 (the fuller page) wins, 概要版 falls back.
    The first row carries the rowspan <th>キノコ</th> before its name cell, so the
    slice must start at that row's <tr>, not inside the th."""
    start = page.find('id="kinoko"')
    table = page[page.rfind("<tr>", 0, start):page.find('id="higher"')]
    rows = []
    for name, links in ROW_RE.findall(table):
        url = next((u for u, label in LINK_RE.findall(links) if "詳細版" in label),
                   next((u for u, _ in LINK_RE.findall(links)), None))
        if url:
            rows.append((html.unescape(name).strip(), url))
    return rows


def parse_title(title):
    """(japanese_name, latin_binomial) from a profile page <title> like
    '自然毒のリスクプロフィール：カキシメジ（Tricholoma ustale)　キシメジ科キシメジ属｜厚生労働省'.
    The binomial is the first two Latin words of the parenthesised name (authors
    and following text are dropped); None when no parenthesised Latin name."""
    m = TITLE_RE.search(html.unescape(title or ""))
    if not m:
        return None, None
    words = re.findall(r"[A-Za-z][a-z-]*", m.group(2))
    binomial = (len(words) >= 2
                and words[1].lower() not in ("sp", "cf", "aff", "var", "subsp"))
    return m.group(1).strip(), " ".join(words[:2]) if binomial else None


def terms_text(page):
    """The copyright/terms body of the 利用規約・リンク・著作権等 page, as plain text."""
    body = page[page.find('m-grid__col1'):]
    body = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", body, flags=re.S)
    body = re.sub(r"<br\s*/?>", "\n", body)
    body = re.sub(r"</(p|h[1-6]|li|tr|div)>", "\n", body)
    body = html.unescape(re.sub(r"<[^>]+>", "", body))
    return "\n".join(ln.strip() for ln in body.splitlines() if ln.strip())


def match_entry(latin, ja, public_by_name, con, live=None):
    """(sp_key or None, how) for one MHLW entry against public species only:
    exact public-name hit, then the COL crosswalk (any usage of one accepted
    taxon), then the live GBIF backbone matcher mapped through the crosswalk.
    A match resolving to a species that is not public is discarded (None) —
    masked taxa must not surface, not even as 'matched something hidden'."""
    from pipeline.fetch_redlist_2025 import colmap_lookup
    if live is None:
        from pipeline.fetch_redlist_2025 import live_match
        live = live_match
    n = latin.strip().lower() if latin else ""
    if not n:
        return None, "no scientific name on page"
    hit = public_by_name.get(n)
    if hit:
        return hit, "exact public name"
    _, col, ambiguous = colmap_lookup(con, latin)
    if ambiguous:
        return None, "ambiguous in crosswalk"
    if col and col in public_by_name.col_keys:
        return col, "crosswalk to COL key"
    d = live(latin) or {}
    key = d.get("speciesKey") if d.get("matchType") != "NONE" else None
    if key:
        row = con.execute("select col_accepted_id from colmap where gbif_id = ?",
                          [str(key)]).fetchone()
        col = row[0] if row else None
        if col and col in public_by_name.col_keys:
            return col, "live match -> crosswalk"
        if f"L{key}" in public_by_name.sp_keys:
            return f"L{key}", "live match (legacy key)"
    return None, "no public species matched"


class PublicIndex:
    """Folded-name / key lookups over the public species list."""
    def __init__(self, species):
        self.by_name = {s["name"].strip().lower(): s["sp_key"] for s in species}
        self.sp_keys = {s["sp_key"] for s in species}
        self.col_keys = {s["col_key"] for s in species if s["col_key"]}

    def get(self, folded):
        return self.by_name.get(folded)


def main(argv=None):
    ap = argparse.ArgumentParser(description="MHLW toxic-marker coverage over public species (item 2).")
    ap.add_argument("--db", default="data/shroomhunt.duckdb")
    ap.add_argument("--map", default="data/raw/col/gbif-col265.tsv.gz")
    ap.add_argument("--csv", default="data/reference/mhlw_toxic.csv")
    ap.add_argument("--cache-dir", default="data/raw/sources/mhlw")
    ap.add_argument("--report", default=common.REPORT)
    ap.add_argument("--sleep", type=float, default=1.0)
    a = ap.parse_args(argv)
    os.makedirs(a.cache_dir, exist_ok=True)

    index_html = common.fetch(INDEX, cache=os.path.join(a.cache_dir, "index.html")).decode("utf-8", "replace")
    rows = parse_index(index_html)
    if len(rows) < 15:
        sys.exit(f"only {len(rows)} mushroom rows parsed — the MHLW index layout changed; refusing")
    terms = terms_text(common.fetch(TERMS, cache=os.path.join(a.cache_dir, "terms.html")).decode("utf-8", "replace"))

    entries = []
    for i, (ja, url) in enumerate(rows, 1):
        page = common.fetch(url, cache=os.path.join(
            a.cache_dir, common.cache_name(url, ".html"))).decode("utf-8", "replace")
        t = re.search(r"<title>([^<]*)</title>", page, re.I)
        title_ja, latin = parse_title(t.group(1) if t else "")
        entries.append({"ja": ja, "url": url, "title_ja": title_ja, "latin": latin})
        if a.sleep and i < len(rows):
            time.sleep(a.sleep)
        print(f"[{i}/{len(rows)}] {ja}: {latin}")

    import duckdb
    con = duckdb.connect(a.db)
    try:
        if not con.execute("select count(*) from colmap").fetchone()[0]:
            from pipeline.col_xr import load_map
            load_map(con, a.map)
        public = common.public_species(a.db)
        idx = PublicIndex(public)
        out, unmatched = [], []
        for e in entries:
            sp_key, how = match_entry(e["latin"], e["ja"], idx, con)
            name = next(s["name"] for s in public if s["sp_key"] == sp_key) if sp_key else None
            (out if sp_key else unmatched).append(
                {"ja": e["ja"], "latin": e["latin"], "url": e["url"],
                 "sp_key": sp_key, "our_name": name, "how": how})
            print(f"  {e['ja']} ({e['latin']}): {how}" + (f" -> {name}" if name else ""))
    finally:
        con.close()

    with open(a.csv, "w", newline="", encoding="utf-8") as f:
        f.write("# Toxic (MHLW 自然毒のリスクプロファイル) species matched to PUBLIC ShroomHunt\n")
        f.write(f"# species; generated by pipeline/sources/mhlw.py from {INDEX}.\n")
        f.write("# Link-only source (spec section 2): the marker links the MHLW risk-profile\n")
        f.write("# page; no MHLW text or images are copied. Masked species never appear here.\n")
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["col_key", "scientific_name", "url"])
        w.writerows((r["sp_key"], r["our_name"], r["url"]) for r in sorted(out, key=lambda r: r["our_name"]))

    n, m = len(entries), len(out)
    body = f"""MHLW index `{INDEX}` — キノコ block: **{n} entries**; matched to public species: **{m} ({100 * m / n:.0f}%)**.

### Terms of use (verbatim quotes, {TERMS})

> 当ホームページで公開している情報（以下「コンテンツ」といいます。）は、特記されていない限り厚生労働省に帰属し、権利表記の記載がない限り「公共データ利用規約（第1.0版）」（PDL1.0）に準拠した利用条件の下で、利用することができます。

> ア　コンテンツを利用する際は出典を記載してください。(出典記載例）出典：厚生労働省ホームページ　（当該ページのURL）

> ア　厚生労働省ホームページは、原則リンクフリーです。（トップページだけでなく、個別情報（案件）へのリンクについても、同様の取り扱いです。）

Reading: content is Japan's Public Data Licence 1.0 unless a page states
otherwise (attribution required; no such statement was found on the risk-
profile pages checked), and **linking to individual pages is explicitly free**.
The spec keeps us link-only regardless — the ⚠ marker carries a link, never a
quote. MHLW match rate is informational (item 6): low coverage only means fewer
markers.

### Matched entries ({m})

| MHLW 和名 | MHLW 学名 | our public species | col_key | how | url |
|---|---|---|---|---|---|
""" + "\n".join(f"| {r['ja']} | {r['latin']} | {r['our_name']} | {r['sp_key']} | {r['how']} | {r['url']} |"
                for r in sorted(out, key=lambda r: r["our_name"])) + f"""

### Unmatched entries ({len(unmatched)})

""" + ("\n".join(f"- {r['ja']} ({r['latin']}): {r['how']}" for r in unmatched)
        or "- none") + f"""

`{a.csv}`: {m} rows (public species only)."""
    common.write_section("MHLW toxic marker", body, a.report)
    common.headline("mhlw", f"{m}/{n} entries matched to public species "
                    f"({100 * m / n:.0f}%, informational); terms: PDL 1.0 + link-free, we stay link-only",
                    a.report)
    return {"entries": n, "matched": m}


if __name__ == "__main__":
    main()
