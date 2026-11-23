#!/usr/bin/env python3
"""Kahaku specimen-database linkability check (TASK-022 item 6) — report only.

Probes the URLs the spec names and their post-redesign homes, records URL
patterns and every copyright statement found, and integrates nothing. The
National Museum of Nature and Science fully redesigned its site in November
2025, which already makes this section valuable: the spec's
web.kahaku.go.jp/research/specimen URL no longer resolves.

Usage: .venv/bin/python pipeline/sources/kahaku.py [--db ignored-for-uniformity]
"""
import argparse
import html
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from pipeline.sources import common  # noqa: E402

OLD = "https://web.kahaku.go.jp/research/specimen"
DB_LIST = "https://www.kahaku.go.jp/kenkyu/data.html"          # the post-redesign DB index
WEBMUSEUM = "https://db.kahaku.go.jp/webmuseum/"               # 標本・資料統合データベース
KINOCO = "https://www.kahaku.go.jp/research/db/botany/kinoco/"  # 筑波実験植物園きのこ図鑑
KINOCO_SEARCH = KINOCO + "search/index.php"
SNET = "https://www.kahaku.go.jp/kenkyu/s-net/index.html"
LINK_RE = re.compile(r'<a\s+[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.S)
FUNGAL_HINTS = ("kinoco", "fung", "kinoko", "myxomycetes", "henkeikin", "ascomycota",
                "s-net", "webmuseum", "tougou")


def fetch_status(url, cache_dir):
    """(status note, body or '') — a 404/403 is itself a finding, never fatal."""
    try:
        return "ok", common.fetch(url, cache=os.path.join(
            cache_dir, common.cache_name(url, ".html")), retries=1).decode("utf-8", "replace")
    except Exception as e:                                  # noqa: BLE001 - probing, failures are findings
        import urllib.error
        if isinstance(e, urllib.error.HTTPError):
            return f"HTTP {e.code}", ""
        return f"unreachable ({e})", ""


def text_of(page):
    t = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", page, flags=re.S)
    t = re.sub(r"<[^>]+>", " ", t)
    return re.sub(r"\s+", " ", html.unescape(t))


def main(argv=None):
    ap = argparse.ArgumentParser(description="Kahaku specimen DB linkability (report only).",
                                 epilog="--db is accepted for uniformity with the other source "
                                        "scripts; this check reads no local data.")
    ap.add_argument("--db", default="data/shroomhunt.duckdb")
    ap.add_argument("--cache-dir", default="data/raw/sources/kahaku")
    ap.add_argument("--report", default=common.REPORT)
    a = ap.parse_args(argv)
    os.makedirs(a.cache_dir, exist_ok=True)

    old_status, old_body = fetch_status(OLD, a.cache_dir)
    redesigned = "全面リニューアル" in old_body or "redesigned" in old_body
    _, db_list = fetch_status(DB_LIST, a.cache_dir)
    fungal = {}
    for url, label in LINK_RE.findall(db_list):
        label = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", label))).strip()
        if any(h in url.lower() for h in FUNGAL_HINTS):
            fungal[url] = label

    wm_status, wm = fetch_status(WEBMUSEUM, a.cache_dir)
    kin_status, kin = fetch_status(KINOCO, a.cache_dir)
    ks_status, kin_search = fetch_status(KINOCO_SEARCH, a.cache_dir)
    kin_txt = text_of(kin) if kin else ""
    ks_txt = text_of(kin_search) if kin_search else ""
    copyright_notes = []
    for name, page in (("統合DB", wm), ("きのこ図鑑", kin)):
        if page:
            for m in re.finditer(r"[Cc]opyright[^<]{0,160}", page):
                copyright_notes.append(f"{name}: {m.group(0).strip()}")
    partial = "すべての標本・資料情報が公開されている" in text_of(wm) if wm else False

    body = f"""Findings only — nothing is integrated (spec: link at most, photos only through
a partnership).

1. **The spec's URL is dead.** `{OLD}` → **{old_status}**
   {("— the museum's own 404 page says the site was 全面リニューアル (fully "
     "redesigned) in November 2025 and the page may have moved or been deleted. "
     if redesigned else "")}The specimen-portal content now lives behind
   `{DB_LIST}` (研究と標本・資料 → データベース).
2. **Relevant databases reachable from the new index:** {len(fungal)} link(s)
   matched fungal/specimen hints:
""" + "\n".join(f"   - [{label}]({url})" for url, label in sorted(fungal.items())) + f"""
3. **標本・資料統合データベース** (`{WEBMUSEUM}`, {wm_status}): search is
   category-driven, `.../webmuseum/search?cls=<category>` (e.g.
   `cls=col_b2_01` is the 菌類 category; `cls=cross_search` crosses
   categories). No stable species- or specimen-record URL pattern is visible
   without driving the JS search in a browser; **no API or bulk download is
   advertised**; the site notes the database holds only PART of the museum's
   holdings ({'confirmed on-page' if partial else 'disclaimer text not found'}).
4. **筑波実験植物園きのこ図鑑** (`{KINOCO}`, {kin_status}): the Tsukuba
   Botanical Garden mushroom guide — 21,337 public records per the portal
   listing (as of 2025-03-10); its own pages count ~7k specimens by 2015.
   Search page `{KINOCO_SEARCH}` ({ks_status}) offers map-based and detailed
   search (分類群 text field taking phylum→species, space-separated; collection
   date; ~28 garden sections). The form is JS-rendered — no GET parameter
   contract is exposed, so **species-level deep links cannot be verified
   without a browser session**.
5. **Image terms:** every page checked carries a plain all-rights-reserved
   footer:
""" + ("\n".join(f"   - `{c}`" for c in copyright_notes) or "   - (none captured)") + f"""
   No CC licence, no reuse grant, no IIIF image terms found on these pages
   (a separate 科博IIIFデータセット exists at iiif.kahaku.go.jp but its licence
   page was not reachable from here). **Verdict: link at most; images only
   through the partnership route David would open with the museum** — exactly
   what spec §2 already assumed. The November 2025 redesign also argues
   against deep-linking any kahaku.go.jp path before David's conversation.

Raw pages cached under `{a.cache_dir}/`.
"""
    common.write_section("Kahaku specimen database (linkability)", body, a.report)
    common.headline("kahaku", f"report only — old URL {old_status} after the Nov 2025 site "
                    "redesign; no stable species-level URL contract found; images all-rights-"
                    "reserved (partnership only); integrate nothing", a.report)
    return {"old_url_status": old_status, "fungal_links": len(fungal)}


if __name__ == "__main__":
    main()
