#!/usr/bin/env python3
"""Resolve the emit_mask_map gaps via the live GBIF species-match service
(TASK-019, David's ruling 2026-11-23) and write the DRAFT overrides CSV for
review. The static crosswalk lags COL: the live service (checklistKey=xcol,
the COL Extended Release) already answers for keys and names the TSV leaves
empty — see https://download.checklistbank.org/col/gbif/README.html, "Looking
up COL identifiers via the GBIF matching service".

Per unmatched gate entry (col_xr.crosswalk_matches misses):
  key  — GET /v2/species/match?checklistKey=xcol&taxonID=gbif:<key>; on NONE
         also try the entry's Red List name (the 7 keys the frozen 2023-08-28
         backbone snapshot does not carry)
  name — GET /v2/species/match?checklistKey=xcol&scientificName=<name>

The col_key recorded is always the ACCEPTED usage's (usage when ACCEPTED,
acceptedUsage when SYNONYM). No parent-species masking without a further
ruling: an infraspecific name whose accepted taxon lacks its final epithet
(i.e. only the parent species matches) records resolution=none
(infraspecific-unresolved), as does anything COL genuinely lacks
(no-col-counterpart). ~1 call per entry, ~1s apart; public API, no auth.

Usage: python3 pipeline/col_resolve_gaps.py [--map data/raw/col/gbif-col265.tsv.gz]
         [--out data/reference/col_mask_overrides.csv] [--sleep 1.0]
         [--mask ... --coverage ... --exceptions ... --redlist ...]        (gate inputs)
"""
import argparse, csv, json, os, sys, time, urllib.error, urllib.parse, urllib.request

try:
    from .col_xr import crosswalk_matches, load_map            # imported as pipeline.col_resolve_gaps
    from .public_taxa import load_gate
except ImportError:                                            # run as a script
    from col_xr import crosswalk_matches, load_map
    from public_taxa import load_gate

MATCH_URL = "https://api.gbif.org/v2/species/match"
MARKERS = {"var", "var.", "f", "f.", "subsp", "subsp.", "ssp", "ssp."}   # rank markers, not epithets
RETRIES = 4


def epithets(name):
    return [t for t in (name or "").lower().replace(".", " . ").split() if t not in MARKERS and t != "."]


def _get(params):
    """One GET against the match service, retried on network/5xx (4xx answers are
    the service saying no and return None)."""
    url = MATCH_URL + "?" + urllib.parse.urlencode(params)
    for attempt in range(1, RETRIES + 1):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "ShroomHunt-pipeline/1.0 (COL gap resolution)"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if 400 <= e.code < 500:
                return None
            print(f"  attempt {attempt}/{RETRIES} failed: {e}", file=sys.stderr)
            time.sleep(2.0 * attempt)
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as e:
            print(f"  attempt {attempt}/{RETRIES} failed: {e}", file=sys.stderr)
            time.sleep(2.0 * attempt)
    sys.exit(f"could not reach {MATCH_URL} after {RETRIES} attempts")


def accepted(d):
    """(col_key, canonical, rank) of the ACCEPTED usage behind a match response,
    or None when the service matched nothing."""
    u = (d or {}).get("usage") or {}
    if not u.get("key") or ((d or {}).get("diagnostics") or {}).get("matchType") == "NONE":
        return None
    if u.get("status") == "SYNONYM":
        a = d.get("acceptedUsage") or {}
        return (a["key"], a.get("canonicalName") or "", a.get("rank") or "") if a.get("key") else None
    return u["key"], u.get("canonicalName") or "", u.get("rank") or ""


def resolve(query, hit):
    """(col_key, reason) for one entry given its name and the service's accepted
    usage: resolved only when the accepted taxon carries the query's own
    epithets — a parent species (or anything coarser) masks nothing."""
    if hit is None:
        return "", "no-col-counterpart"
    key, canonical, rank = hit
    q, c = epithets(query), epithets(canonical)
    if len(q) <= 2:
        if rank == "SPECIES":
            return key, f"accepted {canonical} ({rank.lower()})"
        return "", f"no-col-counterpart: matched only {rank.lower()} {canonical}"
    if c[:len(q)] == q:
        return key, f"accepted {canonical} ({rank.lower()})"
    return "", (f"infraspecific-unresolved: accepted {canonical or rank.lower()}"
                f" lacks the final epithet (parent-species masking needs a ruling)")


def resolve_all(misses, names, sleep):
    """One (kind, value, col_key, method, reason) override row per miss."""
    out = []
    for i, (kind, value, note) in enumerate(misses, 1):
        if note:                                   # ambiguous in the crosswalk: reviewer call
            out.append((kind, value, "", "manual", f"crosswalk {note}"))
            print(f"[{i}/{len(misses)}] {kind} {value}: {note} -> manual row")
            continue
        col_key, reason, method = "", "", ""
        if kind == "key":
            hit = accepted(_get({"checklistKey": "xcol", "taxonID": f"gbif:{value}"}))
            col_key, reason = resolve(names.get(value, ""), hit)
            method = "api-key"
            if not col_key and names.get(value):   # absent from the backbone snapshot: try the name
                time.sleep(sleep)
                hit = accepted(_get({"checklistKey": "xcol", "scientificName": names[value]}))
                col_key, reason = resolve(names[value], hit)
                method = "api-name"
        else:
            hit = accepted(_get({"checklistKey": "xcol", "scientificName": value}))
            col_key, reason = resolve(value, hit)
            method = "api-name"
        out.append((kind, value, col_key, method, reason))
        print(f"[{i}/{len(misses)}] {kind} {value}: {col_key or '-'} ({method}) {reason}")
        if i < len(misses):
            time.sleep(sleep)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="Resolve emit_mask_map gaps via the live "
                                             "GBIF matching service (TASK-019).")
    ap.add_argument("--map", default="data/raw/col/gbif-col265.tsv.gz")
    ap.add_argument("--out", default="data/reference/col_mask_overrides.csv")
    ap.add_argument("--sleep", type=float, default=1.0, help="seconds between API calls")
    ap.add_argument("--mask", default="data/reference/taxon_mask.csv")
    ap.add_argument("--coverage", default="data/reference/redlist_coverage.csv")
    ap.add_argument("--exceptions", default="data/reference/match_exceptions.csv")
    ap.add_argument("--redlist", default="data/raw/redlist/redlist_fungi_jp.json")
    a = ap.parse_args(argv)
    import duckdb
    con = duckdb.connect()                                     # the crosswalk only; no pipeline DB needed
    load_map(con, a.map)
    gate = load_gate(mask=a.mask, coverage=a.coverage, exceptions=a.exceptions, redlist=a.redlist)
    _, misses = crosswalk_matches(con, gate)
    con.close()
    if not misses:
        sys.exit("no gaps: the crosswalk resolves every gate entry (nothing to do)")
    print(f"{len(misses)} gate entries unmatched in the crosswalk; querying {MATCH_URL}")
    names = {str(s["speciesKey"]): s["name"] for s in
             json.load(open(a.redlist, encoding="utf-8"))["species"] if s.get("speciesKey")}
    rows = resolve_all(misses, names, a.sleep)
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["# DRAFT — generated by pipeline/col_resolve_gaps.py via the live GBIF"])
        w.writerow(["# species-match service (checklistKey=xcol / COL XR); pending review; then a"])
        w.writerow(["# reviewed input like taxon_mask.csv. One row per emit_mask_map miss:"])
        w.writerow(["# kind (key|name); value; col_key (empty = stays legacy-masked);"])
        w.writerow(["# method (api-key|api-name|manual); reason. Infraspecific names that only"])
        w.writerow(["# resolve to the parent species stay unresolved by ruling (2026-11-23)."])
        w.writerow(["kind", "value", "col_key", "method", "reason"])
        w.writerows(rows)
    n = sum(1 for r in rows if r[2])
    print(f"{a.out}: {len(rows)} rows, {n} resolved to a COL key, {len(rows) - n} legacy-masked")
    return rows


if __name__ == "__main__":
    main()
