#!/usr/bin/env python3
"""Hosoya et al. 2016 endemic-marker coverage (TASK-022 item 3).

Extracts 表3 (本調査の結果による日本の固有種きのこリスト, rows No 1..71 — the
abstract's "possible endemic" taxa) from the agriknowledge PDF with
`pdftotext -layout`, like fetch_redlist_2025.py. The PDF's embedded fonts have
broken ToUnicode maps, so the Latin names come out garbled (spurious spaces,
Greek/CJK stand-ins like α/冗/ル); rows are therefore recovered structurally
(distribution anchor 日本, No-continuity disambiguation) and the names matched
fuzzily: exact normalized hit against public species, then an 4-gram-prefiltered
coverage match over the COL crosswalk, then the live GBIF backbone matcher.
Match rate below 20% is a STOP condition (spec item 6).

Emits reviewed data/reference/hosoya_endemic_2016.csv
(col_key,scientific_name,source_page) for PUBLIC species only — masked species
appear in no output, not even counts. For each listed species the report also
shows our neighbouring-East-Asia verdict (the local `ea` cells table when the
DB has one, else the `nb` neighbours download): endemic-but-recorded-next-door
conflicts are expected and will be shown, not hidden.

Usage: .venv/bin/python pipeline/sources/hosoya.py [--db ...] [--map ...]
"""
import argparse
import csv
import os
import re
import subprocess
import sys
import time
from difflib import SequenceMatcher

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from pipeline.sources import common  # noqa: E402

URL = "https://agriknowledge.affrc.go.jp/RN/2010910274.pdf"
PDF = "hosoya_2016_endemic.pdf"
MIN_EXPECTED_ROWS = 60          # the abstract counts 71 possible-endemic taxa
STOP_FLOOR = 20                 # spec item 6: match rate below ~20% is a stop
FOOTER_RE = re.compile(r"^\s*[‐‑\-―]\s*(\d{2,3})\s*[ー一‐‑]\s*$")
CJK_RE = re.compile(r"[ぁ-んァ-ヶ一-龠々]")
TRAILING_JA_RE = re.compile(r"[\s、，（）()・？?ー]+$")
# Author surnames glued onto the garbled names (they survive normalization); a
# suffix stripper so '…pseudogemmatahongo' scores against the name, not the cite.
AUTHORS = ("imatsuda", "noordel", "desjardin", "parmasto", "giachini", "kobayasi",
           "verbeken", "imazeki", "matsuda", "curtis", "hongo", "singer", "shirai",
           "yasuda", "henn", "neda", "berk", "sacc", "kuhner", "david", "zhul",
           "yang", "sato", "nara", "kawam", "asai", "loyd", "pouzar", "both",
           "ortiz", "imai", "honda", "otani", "bas", "ito", "ex", "co")


def strip_authors(norm_name):
    """Remove trailing author tokens from a normalized garbled name."""
    changed = True
    while changed:
        changed = False
        for a in AUTHORS:
            if norm_name.endswith(a) and len(norm_name) - len(a) >= 6:
                norm_name = norm_name[:-len(a)]
                changed = True
    return norm_name


def extract_text(pdf, txt_path):
    try:
        subprocess.run(["pdftotext", "-layout", pdf, txt_path], check=True)
    except FileNotFoundError:
        sys.exit("pdftotext not found: install poppler (brew install poppler)")
    return txt_path


def table_region(text):
    """The 表3 lines between the table heading and the *ID footnote, each stamped
    with its printed page number (the running footer ENDS a page: lines above a
    '‑81ー' footer are p.81, lines below it are p.82)."""
    lines = text.splitlines()
    start = next((i for i, ln in enumerate(lines) if re.search(r"表\s*3\s+本調査", ln)), None)
    if start is None:
        sys.exit("表3 heading not found — the PDF layout changed; refusing to write a partial list")
    out, page = [], None
    for ln in lines[start:]:
        if "IDは表" in ln[:20]:
            break
        m = FOOTER_RE.match(ln)
        if m:
            out = [(int(m.group(1)) if p is None else p, l) for p, l in out]
            page = int(m.group(1)) + 1
            continue
        out.append((page, ln))
    return out


def normalize(s):
    """Lowercase a-z only: pdftotext's garbling inserts spaces, punctuation and
    Greek/CJK stand-ins; the surviving letters stay in order."""
    return re.sub(r"[^a-z]", "", (s or "").lower())


def gram_set(s, n=4):
    return {s[i:i + n] for i in range(len(s) - n + 1)}


def parse_rows(region):
    """[{no, id, latin, name_ja, page}] from the 表3 region. Rows are anchored on
    their 分布 cell (every row contains 日本…): the text between two anchors is
    [locality tail][No][ID][garbled current name][和名]. The No/ID digits can be
    split across lines, so the concatenated digit run is disambiguated by
    expecting No = previous + 1 — which doubles as the completeness check."""
    text = "\n".join(l for _, l in region)
    line_starts, o = [], 0                                # line-start offsets -> page
    for _, l in region:
        line_starts.append(o)
        o += len(l) + 1
    line_pages = [p for p, _ in region]

    def page_at(i):
        page = None
        for s, p in zip(line_starts, line_pages):
            if s <= i:
                page = p if p is not None else page
            else:
                break
        return page

    idxs = [m.start() for m in re.finditer(r"日本", text)]
    segments = [(a, text[a:b]) for a, b in zip([0] + idxs, idxs + [len(text)]) if b > a]
    anchors = len(idxs)

    def resolve(digits, tokens, want):
        """(no or None, id, anomaly?) for a No+ID digit run. The PDF text layer
        splits Nos across lines, sometimes flips the two fragments, and once
        dropped a digit outright — the expected No (prev+1) disambiguates all
        three, which is also the completeness check."""
        if digits.startswith(want):
            return int(want), digits[len(want):], False
        if len(tokens) >= 3 and tokens[1] + tokens[0] == want:
            return int(want), "".join(tokens[2:]), False   # flipped split No
        if (len(want) == 2 and digits.startswith(want[0])
                and 1 <= len(digits) - 1 <= 3 and digits[1:].isdigit()):
            return int(want), digits[1:], True             # a No digit was dropped
        return None, digits, True

    rows, prev, anomalies = [], 0, []
    for seg_start, seg in segments[1:]:                   # segment 0 is pre-table
        seg_lines = seg.splitlines()
        # a row's first line starts with No digits followed by space/EOL (wrapped
        # name fragments like "0句 n…" glue digits to CJK — not a row). A segment
        # can open with the PREVIOUS row's wrapped ID digits, so every candidate
        # line is tried until the expected No resolves.
        candidates = [i for i, l in enumerate(seg_lines) if re.match(r"\s*\d+([\s]|$)", l)]
        if not candidates:
            continue                                      # wrapped locality / header only
        picked = None
        for i in candidates:
            tail = "\n".join(seg_lines[i:])
            m = re.match(r"((?:\s*\d+)+)", tail)          # the No+ID digit run
            if not m:
                continue
            tokens = m.group(1).split()
            no, id_digits, odd = resolve("".join(tokens), tokens, str(prev + 1))
            if no is not None:
                picked = (i, m, no, id_digits, odd)
                break
        if picked is None:
            i = candidates[0]
            tail = "\n".join(seg_lines[i:])
            m = re.match(r"((?:\s*\d+)+)", tail)
            if not m:
                continue
            tokens = m.group(1).split()
            no, id_digits, odd = None, "".join(tokens), True
            picked = (i, m, no, id_digits, odd)
        i, m, no, id_digits, odd = picked
        latin = re.search(r"[A-Za-z]", tail[m.end():])
        if latin is None:
            continue
        if no is None:
            anomalies.append(id_digits[:6])
        else:
            prev = no
            if odd:
                anomalies.append(f"No {no} (digit dropped: {id_digits})")
        rest = tail[m.end() + latin.start():]
        body = rest
        while True:                                       # strip the trailing 和名 block
            stripped = TRAILING_JA_RE.sub("", body)
            if stripped and CJK_RE.search(stripped[-1]):
                body = stripped[:-1]
                continue
            body = stripped
            break
        ja_m = re.search(r"([぀-ヿ一-鿿々][぀-ヿ一-鿿々・\s]*[぀-ヿ一-鿿々])", rest)
        # the page of the row's own first line, not of the segment start (the
        # segment can open before a page footer with wrapped-locality lines)
        row_offset = sum(len(l) + 1 for l in seg_lines[:i])
        rows.append({"no": no, "id": id_digits, "latin": body.strip(),
                     "name_ja": (ja_m.group(1).split()[0] if ja_m else "").strip(),
                     "page": page_at(seg_start + row_offset)})
    return rows, anomalies, anchors


def sanity_check(rows, anomalies, anchors):
    """Fail closed before anything is written, like fetch_redlist_2025: every
    分布 anchor must yield a row (a missing row means the layout was misread),
    the parse must meet the abstract's row count, and only a couple of rows may
    carry an unreconcilable No (the flipped-split-digit quirk of this PDF)."""
    if anchors - 1 != len(rows):                          # one anchor is the heading line
        sys.exit(f"layout not fully understood — {anchors} 日本 anchors (rows + heading) "
                 f"but {len(rows)} rows parsed; refusing to write a partial list")
    if len(rows) < MIN_EXPECTED_ROWS:
        sys.exit(f"only {len(rows)} rows parsed (minimum {MIN_EXPECTED_ROWS}); "
                 f"the layout may have changed; refusing to write a partial list")
    if len(anomalies) > 5:
        sys.exit(f"{len(anomalies)} rows with unreconcilable No ({anomalies[:5]}...); "
                 f"the numbering was misread; refusing to write a partial list")
    print(f"parsed {len(rows)} endemic rows from {anchors} anchors "
          f"({len(anomalies)} No-anomalies: {anomalies})")


def match_species_rows(rows, public, con, live=None, threshold=0.80, sleep=1.0):
    """[(row, sp_key or None, matched_name or None, how)] — public species only.
    Tier 1 exact normalized hit on a public species name; tier 2 fuzzy coverage
    match over the crosswalk (colmap), accepted only when the resolved COL id is
    a public species; tier 3 the live GBIF backbone matcher (as
    fetch_redlist_2025.match_species) with the same public-only rule."""
    from pipeline.fetch_redlist_2025 import live_match
    live = live or live_match
    by_norm, col_by_norm = {}, {}
    for s in public:
        by_norm.setdefault(normalize(s["name"]), s["sp_key"])
    for name, col in con.execute(
            "select col_accepted_name, col_accepted_id from colmap "
            "where col_accepted_name <> ''").fetchall():
        col_by_norm.setdefault(normalize(name), col)
    for name, col in con.execute(
            "select gbif_name, col_accepted_id from colmap where gbif_name <> ''").fetchall():
        col_by_norm.setdefault(normalize(name), col)
    public_cols = {s["col_key"] for s in public if s["col_key"]}
    public_spk = {s["sp_key"] for s in public}
    out = []
    for i, row in enumerate(rows, 1):
        g = strip_authors(normalize(row["latin"]))
        g_grams = gram_set(g)
        hit, how = None, "unmatched"
        if len(g) >= 8 and g in by_norm:
            hit, how = by_norm[g], "exact public name"
        else:
            # coverage of the candidate decides accept/reject; among those above
            # the threshold the MOST matched letters win, so the full binomial
            # beats a shorter perfect substring (…pseudogemmata vs gemmata)
            best, best_cov = None, 0.0
            if len(g) >= 10:
                for cand, col in col_by_norm.items():
                    if len(cand) < 10 or len(gram_set(cand) & g_grams) < 3:
                        continue
                    blocks = SequenceMatcher(None, cand, g).get_matching_blocks()
                    matched = sum(b.size for b in blocks)
                    cov = matched / len(cand)
                    if cov < threshold:
                        continue
                    if best is None or matched > best[2]:
                        best, best_cov = (cand, col, matched), cov
            if best:
                if best[1] in public_cols:
                    hit, how = best[1], f"crosswalk fuzzy ({best_cov:.2f})"
                elif best[1]:
                    how = f"matched COL {best[1]} but not a public species here"
                if hit is None and best[0] in by_norm:
                    hit, how = by_norm[best[0]], f"public-name fuzzy ({best_cov:.2f})"
            if hit is None and len(g) >= 10:
                d = live(row["latin"]) or {}
                key = d.get("speciesKey") if d.get("matchType") != "NONE" else None
                if sleep and i < len(rows):
                    time.sleep(sleep)
                if key:
                    r = con.execute("select col_accepted_id from colmap where gbif_id = ?",
                                    [str(key)]).fetchone()
                    col = r[0] if r else None
                    if (col and col in public_cols) or f"L{key}" in public_spk:
                        hit = col if (col and col in public_cols) else f"L{key}"
                        how = f"live match (backbone {key})"
                    else:
                        how = f"live match {key} not public here"
        out.append((row, hit, hit and next((s['name'] for s in public if s['sp_key'] == hit), None), how))
        print(f"[{i}/{len(rows)}] No {row['no']} {row['latin'][:44]}: {how}")
    return out


def east_asia_keys(con):
    """(verdict_source, {sp_key with next-door records}). The ea cells table is
    the intended source (build_db --eastasia); when the DB was built without it,
    the nb neighbours download stands in and the report says so."""
    cols = lambda t: {r[0] for r in con.execute(
        "select column_name from information_schema.columns where table_name = ?", [t]).fetchall()}
    for t, label in (("ea", "ea table (East Asia 1-degree cells)"),
                     ("nb", "nb table (neighbours download; ea absent from this DB)")):
        if t in {r[0] for r in con.execute(
                "select table_name from information_schema.tables").fetchall()}:
            col = "coalesce(col_key, 'L' || speciesKey::varchar)" if "col_key" in cols(t) \
                else "'L' || speciesKey::varchar"
            keys = {r[0] for r in con.execute(
                f"select distinct {col} from {t} where speciesKey is not null").fetchall()}
            return label, keys
    return "no East Asia table in this DB", set()


def main(argv=None):
    ap = argparse.ArgumentParser(description="Hosoya 2016 endemic coverage over public species (item 3).")
    ap.add_argument("--db", default="data/shroomhunt.duckdb")
    ap.add_argument("--map", default="data/raw/col/gbif-col265.tsv.gz")
    ap.add_argument("--raw-dir", default="data/raw/sources/hosoya")
    ap.add_argument("--csv", default="data/reference/hosoya_endemic_2016.csv")
    ap.add_argument("--report", default=common.REPORT)
    ap.add_argument("--sleep", type=float, default=1.0)
    a = ap.parse_args(argv)
    os.makedirs(a.raw_dir, exist_ok=True)

    pdf = os.path.join(a.raw_dir, PDF)
    if not os.path.exists(pdf):
        body = common.fetch(URL, cache=pdf)
        print(f"{pdf}: {len(body):,} bytes")
    txt = extract_text(pdf, pdf.replace(".pdf", ".txt"))
    text = open(txt, encoding="utf-8").read()
    region = table_region(text)
    rows, anomalies, anchors = parse_rows(region)
    sanity_check(rows, anomalies, anchors)

    import duckdb
    con = duckdb.connect(a.db)
    try:
        if not con.execute("select count(*) from colmap").fetchone()[0]:
            from pipeline.col_xr import load_map
            load_map(con, a.map)
        public = common.public_species(a.db)
        matched = match_species_rows(rows, public, con, sleep=a.sleep)
        ea_label, ea_keys = east_asia_keys(con)
    finally:
        con.close()

    hits = [(r, k, n, how) for r, k, n, how in matched if k]
    with open(a.csv, "w", newline="", encoding="utf-8") as f:
        f.write("# Endemic (固有) marker input: Hosoya T., Uzuhashi S., Hosaka K., Kudo S. 2016.\n")
        f.write("# 日本産固有菌類目録の作成に向けて. Trans. Mycol. Soc. Japan 57(2): 77-84.\n")
        f.write(f"# {URL} — 表3, the paper's possible-endemic list. Cite on every use.\n")
        f.write("# Generated by pipeline/sources/hosoya.py; PUBLIC species only (masked species\n")
        f.write("# never appear). The PDF's text layer is garbled (broken font ToUnicode maps),\n")
        f.write("# so names were recovered by fuzzy match — REVIEW against the PDF before use.\n")
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["col_key", "scientific_name", "source_page"])
        w.writerows((k, n, f"p.{r['page'] or '?'}") for r, k, n, how in
                    sorted(hits, key=lambda h: h[2] or ""))

    n, m = len(rows), len(hits)
    conflicts = [h for h in hits if h[1] in ea_keys]
    rate = 100 * m / n if n else 0
    body = f"""Hosoya et al. 2016 表3 (possible-endemic list): **{n} rows**, matched to public
species: **{m} ({rate:.0f}%)**. Citation (shown with every 固有 marker):
Hosoya T., Uzuhashi S., Hosaka K., Kudo S. 2016. 日本産固有菌類目録の作成に向けて.
*Trans. Mycol. Soc. Japan* 57(2): 77–84. {URL}

Extraction caveat: the agriknowledge PDF's embedded fonts have broken ToUnicode
maps, so `pdftotext -layout` garbles the Latin names (spurious spaces; α/冗/ル
stand-ins). Rows were recovered structurally (anchor on the 分布 日本 cell; the
No/ID digit run disambiguated by No = previous+1, which doubles as the
completeness check — parse fails closed on any gap) and the names matched
fuzzily (normalized-letter coverage ≥ 0.80 over the COL crosswalk, live GBIF
backbone as the last resort). The CSV is **pending review** against the PDF.

### Matched ({m}) — 和名 from the PDF, our public species, next-door verdict

| No | PDF 現在名 (garbled → best effort) | 和名 | our species | how matched | recorded next door |
|---|---|---|---|---|---|
""" + "\n".join(f"| {r['no']} | {r['latin'][:40]} | {r['name_ja']} | {n or ''} | {how} | "
                f"{'**yes (conflict)**' if k in ea_keys else 'no'} |"
                for r, k, n, how in matched if k) + f"""

Conflicts (endemic per Hosoya 2016, but recorded next door by our data —
expected and will be shown, not hidden): **{len(conflicts)} of {m}**,
via the {ea_label}.

### Unmatched rows ({n - m})

""" + ("\n".join(f"- No {r['no']} {r['latin'][:44]} ({r['name_ja']}): {how}"
                for r, k, n, how in matched if not k) or "- none") + f"""

Stop condition (spec item 6): Hosoya match rate must be ≥ ~{STOP_FLOOR}%.
**Verdict: {rate:.0f}% — {'ABOVE' if rate >= STOP_FLOOR else 'BELOW'} the floor.**
`{a.csv}`: {m} rows (public species only)."""
    common.write_section("Hosoya 2016 endemic marker", body, a.report)
    common.headline("hosoya", f"{m}/{n} rows matched ({rate:.0f}%, floor {STOP_FLOOR}%: "
                    f"{'PASS' if rate >= STOP_FLOOR else 'STOP'}); {len(conflicts)} endemic-but-next-door "
                    f"conflicts (expected)", a.report)
    return {"rows": n, "matched": m, "conflicts": len(conflicts)}


if __name__ == "__main__":
    main()
