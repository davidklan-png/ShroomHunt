#!/usr/bin/env python3
"""Shared base for the species-source coverage scripts (TASK-022, spec item 6).

Every script under pipeline/sources/ measures one external source against the
PUBLIC species of a local pipeline DB and writes its section into
data/raw/sources/coverage_report.md (gitignored; counts and percentages only in
the task Report). Masked species appear in no output file, not even counts: the
species list comes from the same gate build_dashboard_data.py installs, and
source scripts may only ever join against it.

public_species() is the gate, verbatim: col_xr.ensure/exprs/keyed plus
public_taxa.load_gate (col_mask_map included on a COL-keyed DB, fail closed)
and gate.sql_where("jp"), grouped by sp_key = coalesce(col_key, 'L'||speciesKey).
"""
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from pipeline.col_xr import ensure, exprs, keyed  # noqa: E402
from pipeline.public_taxa import load_gate  # noqa: E402

# The WDQS/Commons/MediaWiki terms require a descriptive UA; MHLW and the other
# sources get the same courtesy string.
UA = ("ShroomHunt-species-sources/1.0 (fungi dashboard species-page coverage "
      "study; https://github.com/davidklan-png/ShroomHunt)")
REPORT = "data/raw/sources/coverage_report.md"
RETRIES, BACKOFF = 4, 2.0
GATE_INPUTS = {"mask": "data/reference/taxon_mask.csv",
               "coverage": "data/reference/redlist_coverage.csv",
               "exceptions": "data/reference/match_exceptions.csv",
               "redlist": "data/raw/redlist/redlist_fungi_jp.json",
               "col_mask": "data/reference/col_mask_map.csv"}


def public_species(db):
    """[{sp_key, col_key, name, genus, family, legacy_keys}] — the species whose
    details may publish, gated exactly as build_dashboard_data.py counts them.
    legacy_keys are the backbone keys Wikidata P846 still uses (several when COL
    lumped synonyms into one accepted taxon)."""
    import duckdb
    con = duckdb.connect(db)
    try:
        ensure(con)
        gate = load_gate(col_mask=GATE_INPUTS["col_mask"] if keyed(con) else None,
                         **{k: v for k, v in GATE_INPUTS.items() if k != "col_mask"})
        gate.install_sql(con)
        jp_cols = {r[0] for r in con.execute(
            "select column_name from information_schema.columns where table_name='jp'").fetchall()}
        X = exprs("jp", jp_cols)
        rows = con.execute(f"""select {X['sp_key']}, {X['col_key']}, {X['sp_name']},
                                      {X['genus']}, {X['family']},
                                      string_agg(distinct speciesKey::varchar, '|')
                               from jp where speciesKey is not null and {gate.sql_where("jp")}
                               group by 1,2,3,4,5""").fetchall()
    finally:
        con.close()
    return [{"sp_key": r[0], "col_key": r[1], "name": r[2], "genus": r[3], "family": r[4],
             "legacy_keys": sorted(r[5].split("|"), key=int)} for r in rows]


def fetch(url, cache=None, params=None, headers=None, ua=UA, data=None, retries=RETRIES):
    """HTTP GET (or POST with urlencoded `data`) with retries and a disk cache.
    Returns the response body as bytes; 4xx raises urllib.error.HTTPError so
    probe-style callers can catch it. The cache is authoritative once written —
    re-runs never re-hit the API."""
    if cache and os.path.exists(cache) and os.path.getsize(cache) > 0:
        with open(cache, "rb") as f:
            return f.read()
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, data=data, headers={"User-Agent": ua, **(headers or {})})
    err = None
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                body = r.read()
                if cache:
                    os.makedirs(os.path.dirname(cache) or ".", exist_ok=True)
                    tmp = cache + ".tmp"
                    with open(tmp, "wb") as f:
                        f.write(body)
                    os.replace(tmp, cache)
                return body
        except urllib.error.HTTPError as e:
            if 400 <= e.code < 500:
                raise                                    # client error: not retryable
            err = e
        except (urllib.error.URLError, OSError) as e:
            err = e
        print(f"  attempt {attempt}/{retries} failed: {e}", file=sys.stderr)
        time.sleep(BACKOFF * attempt)
    raise err


def fetch_json(url, **kw):
    return json.loads(fetch(url, **kw).decode("utf-8"))


def cache_name(url, suffix=""):
    """Stable filename for a cached response: domain + sha1 of the URL."""
    host = urllib.parse.urlparse(url).netloc.split(":")[0]
    return f"{host}-{hashlib.sha1((url + suffix).encode()).hexdigest()[:16]}{suffix}"


# ---------- the shared report -----------------------------------------------------------------

def _read_sections(report):
    """{'Title': 'body'} in file order; anything before the first '## ' is discarded
    (write_section rewrites the standard preamble)."""
    if not os.path.exists(report):
        return {}
    text = open(report, encoding="utf-8").read()
    secs, title = {}, None
    for ln in text.splitlines():
        m = re.match(r"^## (.+)$", ln)
        if m:
            title = m.group(1)
            secs[title] = []
        elif title is not None:
            secs[title].append(ln)
    return {t: "\n".join(b).strip("\n") for t, b in secs.items()}


def _write_sections(secs, report):
    os.makedirs(os.path.dirname(report) or ".", exist_ok=True)
    with open(report, "w", encoding="utf-8") as f:
        f.write("# Species-sources coverage report (TASK-022, spec item 6)\n\n")
        f.write("Public species only, measured against the local pipeline DB through the\n"
                "shared taxon gate. Masked taxa appear nowhere here, not even in counts.\n\n")
        for t, b in secs.items():
            f.write(f"## {t}\n\n{b}\n\n")


def write_section(title, body, report=REPORT):
    """Replace (or append) one '## title' section; re-runnable in any order."""
    secs = _read_sections(report)
    secs[title] = body.strip("\n")
    _write_sections(secs, report)
    print(f"{report}: section '{title}' written ({len(body.splitlines())} lines)")


def headline(source, line, report=REPORT):
    """One 'source: verdict' line in the Headline section, kept sorted; re-running
    a script replaces its own line only."""
    secs = _read_sections(report)
    lines = [l for l in secs.get("Headline", "").splitlines()
             if l and not l.startswith(source + ":")]
    lines.append(f"{source}: {line}")
    secs["Headline"] = "\n".join(sorted(lines))
    rest = {t: b for t, b in secs.items() if t != "Headline"}
    _write_sections({"Headline": secs["Headline"], **rest}, report)
