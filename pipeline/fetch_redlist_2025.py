#!/usr/bin/env python3
"""Download the Ministry of the Environment 5th Red List for fungi (2025) and
make it the masking input (TASK-020, spec §0a), replacing the 2020 GBIF
checklist download as what data/raw/redlist/redlist_fungi_jp.json holds.

Source PDF: https://www.env.go.jp/nature/kisho/5th-rl-2025-book/05-5thredlist-fungi.pdf
(government publication; extracted names/categories are facts, the file is kept
in gitignored data/raw/ like every raw download). Last-Modified sidecar skip as
in fetch_col_mapping.py; extraction via `pdftotext -layout` (poppler-utils).

Steps: download -> parse rows (category, Japanese name, scientific name(s)) ->
data/reference/redlist_2025_parsed.csv (reviewed) -> match each scientific name:
the COL crosswalk first (lower(gbif_name)=name or lower(col_accepted_name)=name
-> legacy gbif_id; >1 accepted taxon -> AMBIGUOUS), then the live GBIF backbone
species/match (kingdom=Fungi), the returned key mapped through the crosswalk.
Still nothing -> matchType NONE, speciesKey null (the gate keeps masking the
NAME, fail-closed; only the key is missing).

Transition rule (spec: keep 2020 as a superset during the transition if that is
safer): the current download is archived as redlist_fungi_jp_2020.json
(pipeline/fetch_redlist.py remains its regenerator); when every 2025 name
matched (speciesKey or AMBIGUOUS) the main file is pure 2025, otherwise it is
the UNION of both lists — an unmatched 2025 name that also appears in 2020
keeps its 2020 key, and 2020-only names stay listed, so nothing un-masks by
accident. The coverage manifest row is appended for review, and
data/raw/redlist/redlist_2025_diff.md (private, gitignored) carries the
added/removed names, unmatched reasons and the masked-distinct before/after
(the latter needs the local pipeline DB; skipped where it is absent).

Usage: python3 pipeline/fetch_redlist_2025.py [--out-dir data/raw/redlist]
        [--parsed data/reference/redlist_2025_parsed.csv] [--map data/raw/col/gbif-col265.tsv.gz]
        [--diff data/raw/redlist/redlist_2025_diff.md] [--db data/shroomhunt.duckdb]
        [--sleep 1.0] [--force]
"""
import argparse, csv, json, os, re, shutil, subprocess, sys, time, urllib.error, urllib.parse, urllib.request

try:
    from .col_xr import load_map                               # imported as pipeline.fetch_redlist_2025
except ImportError:                                            # run as a script
    from col_xr import load_map

URL = "https://www.env.go.jp/nature/kisho/5th-rl-2025-book/05-5thredlist-fungi.pdf"
PDF = "05-5thredlist-fungi.pdf"
MATCH_API = "https://api.gbif.org/v1/species/match"
MAIN = "redlist_fungi_jp.json"
OLD = "redlist_fungi_jp_2020.json"
OLD_DOI = "10.15468/kmv4xm"          # the 2020 GBIF checklist download (fetch_redlist.py)
PURE = "redlist_fungi_jp_2025_pure.json"
RETRIES, BACKOFF = 4, 2.0
# ponytail: the 4th (2020) list document carried ~1.3k names per the task brief; a
# parse far below every previous edition means the layout changed and was misread.
MIN_EXPECTED_ROWS = 100

ROW_RE = re.compile(r"^(FU\d{4})\s+(.+)$")
CODE_RE = re.compile(r"[（(]([A-Za-z]+)[）)]")
CHROME_RE = (re.compile(r"^環境省.+レッドリスト"),             # title, column header, page
             re.compile(r"^掲載No\."),                        # footer: known non-rows, not
             re.compile(r"^\d+ / \d+ ページ$"))               # "unparsed" leftovers


def download(out_dir, force=False):
    """The Red List PDF with a Last-Modified sidecar skip (see fetch_col_mapping.py)."""
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, PDF)
    sidecar = path + ".last-modified"
    headers = {"User-Agent": "ShroomHunt-pipeline/1.0 (5th Red List switch)"}
    if not force and os.path.exists(path) and os.path.exists(sidecar):
        headers["If-Modified-Since"] = open(sidecar).read().strip()
    req = urllib.request.Request(URL, headers=headers)
    for attempt in range(1, RETRIES + 1):
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                if r.status == 304:
                    print(f"{path}: unchanged (HTTP 304); skipping download")
                    return path
                body = r.read()
                if not body.startswith(b"%PDF"):
                    raise OSError(f"not a PDF ({len(body)} bytes) on attempt {attempt}")
                tmp = path + ".tmp"
                with open(tmp, "wb") as f:
                    f.write(body)
                os.replace(tmp, path)
                lm = r.headers.get("Last-Modified")
                if lm:
                    open(sidecar, "w").write(lm + "\n")
                print(f"{path}: downloaded {len(body):,} bytes (Last-Modified {lm or 'unknown'})")
                return path
        except urllib.error.HTTPError as e:
            if e.code == 304:                                  # urllib raises instead of returning 304
                print(f"{path}: unchanged (HTTP 304); skipping download")
                return path
            print(f"attempt {attempt}/{RETRIES} failed: {e}", file=sys.stderr)
            time.sleep(BACKOFF * attempt)
        except OSError as e:
            print(f"attempt {attempt}/{RETRIES} failed: {e}", file=sys.stderr)
            time.sleep(BACKOFF * attempt)
    sys.exit(f"could not download {URL} after {RETRIES} attempts")


def extract_text(pdf, txt_path):
    try:
        subprocess.run(["pdftotext", "-layout", pdf, txt_path], check=True)
    except FileNotFoundError:
        sys.exit("pdftotext not found: install poppler-utils (brew install poppler / "
                 "sudo apt-get install -y poppler-utils) — never a Python dependency")
    return txt_path


def parse_rows(text):
    """(entries, stats) from `pdftotext -layout` output. One entry per scientific
    name; a row carrying several names yields several entries. stats["gaps"] lists
    numbering jumps and stats["other"] the lines that matched no row — both must be
    empty for stats["complete"], otherwise rows were lost and the parse is partial."""
    entries, other, gaps, last = [], [], [], 0
    for ln in text.splitlines():
        s = ln.strip()
        if not s:
            continue
        m = ROW_RE.match(s)
        if not m:
            if not any(c.match(s) for c in CHROME_RE):
                other.append(s)
            continue
        no = int(m.group(1)[2:])
        if last and no != last + 1:
            gaps.append((last, no))
        last = no
        parts = [p for p in re.split(r"\s{2,}", m.group(2).strip()) if p]
        if len(parts) < 2:
            other.append(s)                                    # not a usable row after all
            continue
        cat_ja = parts[0]
        code = (CODE_RE.search(cat_ja) or [None, ""])[1].upper()
        if len(parts) == 2:                                    # a long 和名 ate its column gap:
            at = re.search(r"[A-Za-z]", parts[1])              # split at the first Latin letter
            if not at or at.start() == 0:
                other.append(s)
                continue
            parts = [cat_ja, parts[1][:at.start()].strip(), parts[1][at.start():]]
        for sci in parts[2:]:
            sci = sci.strip(" ，,;；")
            if sci:
                entries.append({"no": no, "category_ja": cat_ja, "category_code": code,
                                "name_ja": parts[1], "scientific_name": sci})
    stats = {"rows": len(entries), "gaps": gaps, "other": other,
             "complete": not gaps and not other}
    return entries, stats


def sanity_check(stats, printed):
    """Fail closed before anything is written (PR #3 review P1): the 2020 union
    cannot protect newly listed 2025 taxa a broken parse omits, so a partial
    list must block, not warn."""
    if not stats["complete"]:
        sys.exit(f"layout not fully understood — numbering gaps {stats['gaps']}, "
                 f"unparsed lines {stats['other']}; refusing to write a partial list")
    if stats["rows"] < MIN_EXPECTED_ROWS:
        sys.exit(f"only {stats['rows']} names parsed (minimum {MIN_EXPECTED_ROWS}); "
                 f"the layout may have changed; refusing to write a partial list")
    print(f"parsed {stats['rows']} scientific names from {printed}")


def write_parsed_csv(entries, path):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        f.write(f"# Generated by pipeline/fetch_redlist_2025.py from the Ministry of the\n")
        f.write(f"# Environment 5th Red List (fungi, 2025): {URL}\n")
        f.write("# One row per scientific name; a Red List row listing several names\n")
        f.write("# yields several rows. Reviewed reference input, like taxon_mask.csv.\n")
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["category_ja", "category_code", "name_ja", "scientific_name"])
        w.writerows((e["category_ja"], e["category_code"], e["name_ja"], e["scientific_name"])
                    for e in entries)
    print(f"{path}: {len(entries)} rows")


def colmap_lookup(con, name):
    """(legacy gbif_id, col_accepted_id, ambiguous) for a folded name against the
    crosswalk; several usages of ONE accepted taxon are fine (the accepted-status
    usage wins the legacy key), several accepted taxa are not."""
    n = name.strip().lower()
    rows = con.execute("select gbif_id, col_status, col_accepted_id from colmap "
                       "where lower(gbif_name) = ? or lower(col_accepted_name) = ?",
                       (n, n)).fetchall()
    if len({r[2] for r in rows}) > 1:
        return None, None, True
    if not rows:
        return None, None, False
    best = sorted(rows, key=lambda r: (r[1] != "accepted", r[0]))[0]
    return best[0], best[2], False


def live_match(name):
    """The live GBIF backbone species/match (v1, as fetch_redlist.py used it);
    None when the service answers no match."""
    url = MATCH_API + "?" + urllib.parse.urlencode({"name": name, "kingdom": "Fungi"})
    for attempt in range(1, RETRIES + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "ShroomHunt-pipeline/1.0"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if 400 <= e.code < 500:
                return None
            print(f"  attempt {attempt}/{RETRIES} failed: {e}", file=sys.stderr)
            time.sleep(BACKOFF * attempt)
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as e:
            print(f"  attempt {attempt}/{RETRIES} failed: {e}", file=sys.stderr)
            time.sleep(BACKOFF * attempt)
    sys.exit(f"could not reach {MATCH_API} after {RETRIES} attempts")


def match_species(names, con, live=live_match, sleep=1.0):
    """(name, speciesKey, matchType, col_accepted_id) per name, colmap first, live
    service second (its backbone key mapped through the crosswalk), NONE last."""
    out, live_hits = [], 0
    for i, name in enumerate(names, 1):
        key, col, ambiguous = colmap_lookup(con, name)
        if ambiguous:
            mt = "AMBIGUOUS"
        elif key:
            mt = "EXACT"
        else:
            d = live(name) or {}
            key, mt = d.get("speciesKey"), d.get("matchType")
            live_hits += 1
            if sleep and i < len(names):
                time.sleep(sleep)
            if not key or mt == "NONE":
                key, mt = None, "NONE"
            else:
                hit = con.execute("select col_accepted_id from colmap where gbif_id = ?",
                                  [str(key)]).fetchone()
                col = hit[0] if hit else None
        out.append((name, str(key) if key else None, mt, col))
        print(f"[{i}/{len(names)}] {name}: {mt}" + (f" key {key} -> col {col}" if key else ""))
    n_col = sum(1 for _, k, mt, c in out if k and c and mt != "NONE")
    print(f"matched {sum(1 for r in out if r[1])} of {len(out)} names "
          f"({live_hits} needed the live service, {n_col} keys map into the crosswalk)")
    return out


def write_json(path, species, fetched=None):
    d = {"dataset": URL, "doi": None, "fetched": fetched or time.strftime("%Y-%m-%d"),
         "species": species}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    return path


def transition(pure_species, old_path, main_path):
    """Archive the incumbent 2020 download as ..._2020.json and write the main
    file: pure 2025 when everything matched, else the union with 2020
    (fail-closed). Returns the decision string for the Report."""
    archive = os.path.join(os.path.dirname(main_path) or ".", OLD)
    old_species = []
    src = archive if os.path.exists(archive) else old_path
    if os.path.exists(src):
        with open(src, encoding="utf-8") as f:
            d = json.load(f)
        if src == archive or d.get("doi") == OLD_DOI:          # only the 2020 checklist ever
            old_species = d["species"]                          # unions/archives; a re-run's
            if src != archive:                                  # main file must not overwrite it
                shutil.copyfile(src, archive)
                print(f"{archive}: 2020 download archived for provenance")
    if all(s.get("speciesKey") or s.get("matchType") == "AMBIGUOUS" for s in pure_species):
        write_json(main_path, pure_species)
        print(f"{main_path}: pure 2025 ({len(pure_species)} names, all matched)")
        return "pure-2025"
    if not old_species:
        sys.exit(f"unmatched 2025 names but no 2020 download to union with at {old_path}; "
                 "run pipeline/fetch_redlist.py first (fail-closed, spec §0a transition rule)")
    old_by_name = {}
    for s in old_species:                                      # a 2025 category wins; a 2025 miss
        old_by_name.setdefault(s["name"].strip().lower(), s)   # keeps the 2020 key
    union = []
    for s in pure_species:
        o = old_by_name.pop(s["name"].strip().lower(), None)
        if o and not s.get("speciesKey"):
            s = {**s, "speciesKey": o.get("speciesKey"), "matchType": o.get("matchType")}
        union.append(s)
    union += list(old_by_name.values())                        # 2020-only names keep masking
    write_json(main_path, union)
    print(f"{main_path}: UNION of 2020 and 2025 ({len(union)} names; "
          f"{len(old_by_name)} 2020-only names retained) — nothing un-masks by accident")
    return "union-2020+2025"


def append_coverage(main_path, coverage="data/reference/redlist_coverage.csv"):
    """The manifest row for the new download, for review (public_taxa.py's own
    __main__ prints the same line); appended only when its sha is not there yet."""
    try:
        from public_taxa import coverage_row                    # run as a script
    except ImportError:
        from .public_taxa import coverage_row
    row = coverage_row(main_path)
    text = open(coverage, encoding="utf-8").read()
    if row["sha256"] in text:
        print(f"{coverage}: row for this download already present")
    else:
        with open(coverage, "a", encoding="utf-8") as f:
            f.write(",".join(row.values()) + "\n")
        print(f"{coverage}: appended {','.join(row.values())} (pending review)")


def diff_report(main_path, pure_species, entries, out_path, db=None, decision="",
                mask="data/reference/taxon_mask.csv",
                coverage="data/reference/redlist_coverage.csv",
                exceptions="data/reference/match_exceptions.csv"):
    """Private diff (names never enter the repo): added/removed vs the archived
    2020 download, unmatched names with reasons, masked-distinct before/after."""
    old_path = os.path.join(os.path.dirname(main_path) or ".", OLD)
    fold = lambda n: (n or "").strip().lower()
    old = ({fold(s["name"]): s for s in
            json.load(open(old_path, encoding="utf-8"))["species"]}
           if os.path.exists(old_path) else {})
    new = {fold(s["name"]): s for s in pure_species}
    cat = {fold(e["scientific_name"]): f'{e["category_code"]} {e["name_ja"]}' for e in entries}
    added = sorted(set(new) - set(old))
    removed = sorted(set(old) - set(new))
    unmatched = [(s["name"], s["matchType"]) for s in pure_species if not s.get("speciesKey")]
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(f"# 2025 Red List masking switch (TASK-020) — private, gitignored; "
                f"per-species names stay out of the repo\n\n")
        f.write(f"2025 names {len(new)}, 2020 names {len(old)}; "
                f"added {len(added)}, removed {len(removed)}, unmatched {len(unmatched)}\n")
        if decision == "union-2020+2025":
            f.write("Main file: UNION with 2020 — the removed names below stay masked "
                    "during the transition; only a pure-2025 switch would unlist them\n")
        f.write("\n")
        f.write(f"## Added vs 2020: {len(added)}\n\n")
        for n in added:
            f.write(f"- {n} ({cat.get(n, '?')}) [{new[n]['matchType']}]\n")
        f.write(f"\n## Removed vs 2020: {len(removed)}\n\n")
        for n in removed:
            f.write(f"- {n} ({old[n].get('matchType')})\n")
        f.write(f"\n## Unmatched or ambiguous in 2025: {len(unmatched)} "
                f"(masked by name only, no key)\n\n")
        for n, mt in unmatched:
            f.write(f"- {n} [{mt}]\n")
        f.write(f"\n## Masked distinct (jp records): see below\n")
    masked = masked_before_after(old_path, main_path, db, mask, coverage, exceptions)
    if masked:
        with open(out_path, "a", encoding="utf-8") as f:
            f.write(f"\nmasked distinct before {masked[0]} -> after {masked[1]} "
                    f"(public {masked[2]} -> {masked[3]}; name/legacy-key gating on both "
                    f"sides — COL-key gating is the col_mask_map's own report)\n")
    print(f"{out_path}: added {len(added)}, removed {len(removed)}, "
          f"unmatched {len(unmatched)}, masked distinct {masked}")
    return {"added": len(added), "removed": len(removed), "unmatched": len(unmatched),
            "masked": masked}


def masked_before_after(old_path, main_path, db, mask, coverage, exceptions):
    """Distinct jp species masked by the 2020 gate vs the new one, legacy gating on
    both sides; None when the pipeline DB is not around (CI runners)."""
    if not (db and os.path.exists(db) and os.path.exists(old_path)):
        return None
    import duckdb
    try:
        from public_taxa import load_gate
    except ImportError:
        from .public_taxa import load_gate
    con = duckdb.connect(db, read_only=True)
    rows = con.execute("select distinct speciesKey::varchar, species, genus, taxonRank "
                       "from jp where speciesKey is not null").fetchall()
    con.close()
    out = []
    for redlist in (old_path, main_path):
        gate = load_gate(mask=mask, coverage=coverage, exceptions=exceptions, redlist=redlist)
        m = {k for k, sp, g, r in rows
             if not gate.is_public(species=sp, species_key=k, genus=g, taxon_rank=r)}
        out.append(len(m))
    pub = lambda i: len({k for k, *_ in rows}) - out[i]
    return out[0], out[1], pub(0), pub(1)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Switch the masking input to the 2025 Red List (TASK-020).")
    ap.add_argument("--out-dir", default="data/raw/redlist")
    ap.add_argument("--parsed", default="data/reference/redlist_2025_parsed.csv")
    ap.add_argument("--map", default="data/raw/col/gbif-col265.tsv.gz")
    ap.add_argument("--diff", default="data/raw/redlist/redlist_2025_diff.md")
    ap.add_argument("--db", default="data/shroomhunt.duckdb")
    ap.add_argument("--coverage", default="data/reference/redlist_coverage.csv")
    ap.add_argument("--sleep", type=float, default=1.0, help="seconds between live API calls")
    ap.add_argument("--force", action="store_true", help="ignore the stored Last-Modified")
    a = ap.parse_args(argv)

    pdf = download(a.out_dir, a.force)
    txt = extract_text(pdf, os.path.join(a.out_dir, PDF.replace(".pdf", ".txt")))
    with open(txt, encoding="utf-8") as f:
        entries, stats = parse_rows(f.read())
    sanity_check(stats, PDF)
    write_parsed_csv(entries, a.parsed)

    import duckdb
    con = duckdb.connect()                                     # the crosswalk only, as col_resolve_gaps
    load_map(con, a.map)
    names, seen = [], set()
    for e in entries:                                          # dedupe folded names, keep list order
        n = e["scientific_name"].strip().lower()
        if n not in seen:
            seen.add(n)
            names.append(e["scientific_name"])
    matched = match_species(names, con, sleep=a.sleep)
    con.close()
    status = {e["scientific_name"].strip().lower(): e["category_code"] for e in entries}
    pure = [{"name": n, "status": status[n.strip().lower()],
             "speciesKey": int(k) if k and str(k).isdigit() else k, "matchType": mt}
            for n, k, mt, _ in matched]
    write_json(os.path.join(a.out_dir, PURE), pure)
    print(f"{os.path.join(a.out_dir, PURE)}: {len(pure)} species")

    main_path = os.path.join(a.out_dir, MAIN)
    decision = transition(pure, main_path, main_path)
    append_coverage(main_path, a.coverage)
    if a.diff:
        diff_report(main_path, pure, entries, a.diff, db=a.db, decision=decision,
                    coverage=a.coverage)


if __name__ == "__main__":
    main()
