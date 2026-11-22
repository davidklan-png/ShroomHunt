#!/usr/bin/env python3
"""COL XR re-keying (TASK-018, spec §0b): GBIF's legacy Backbone is frozen, so
every public output moves its species identity to the Catalogue of Life
eXtended Release while keeping the legacy key as a secondary field (Wikidata
P846 still uses legacy keys).

Crosswalk: the static gbif-col265.tsv.gz (COL 26.5 XR against the backbone of
2023-08-28; pipeline/fetch_col_mapping.py). One row per backbone usage; empty
col: columns mean no COL match. gbif:scientificName is canonical (no authorship)
and col:acceptedID/Name are empty for accepted usages, so the accepted identity
is resolved at load: col_accepted_id = col:acceptedID or col:ID, and
col_accepted_name = col:acceptedScientificName or col:scientificName. The
col:kingdom…col:genus columns are the ACCEPTED taxon's classification, which is
what the builders switch their genus/family/order/class grouping to (null keeps
the backbone value; rows are never dropped).

Species identity after re-keying (builders): sp_key = coalesce(col_key,
'L' || speciesKey) — two backbone synonyms lumped under one COL id count and
list once — and sp_name = coalesce(col_name, species).

Fail closed: emit_mask_map raises PublicTaxaError when a keyed Red List entry,
a commercial mask row or an alias resolves to no COL accepted taxon; the gate
(public_taxa.load_gate(col_mask=...)) then refuses to load until the mask map
is regenerated and reviewed.

Usage: python3 pipeline/col_xr.py --db data/shroomhunt.duckdb --map data/raw/col/gbif-col265.tsv.gz
         [--mask-map data/reference/col_mask_map.csv] [--diff data/raw/col/col_xr_diff.md]
         [--mask ... --coverage ... --exceptions ... --redlist ...]        (gate inputs)
"""
import argparse, csv, gzip, os, sys

try:
    from .public_taxa import PublicTaxaError, load_gate   # imported as pipeline.col_xr
except ImportError:                                       # run as a script: python3 pipeline/col_xr.py
    from public_taxa import PublicTaxaError, load_gate

URL = "https://download.checklistbank.org/col/gbif/gbif-col265.tsv.gz"
TABLES = ("jp", "nb", "mo", "ea")            # the tables that carry speciesKey
COL_COLUMNS = ("col_key", "col_name", "col_family", "col_order", "col_class", "col_phylum", "col_genus")
TSV_FIELDS = {"gbif_id": "gbif:ID", "rank": "gbif:rank", "gbif_name": "gbif:scientificName",
              "kingdom": "gbif:kingdom", "col_id": "col:ID", "col_status": "col:status",
              "col_name": "col:scientificName", "accepted_id": "col:acceptedID",
              "accepted_name": "col:acceptedScientificName", "family": "col:family",
              "order": "col:order", "class": "col:class", "phylum": "col:phylum", "genus": "col:genus"}


def load_map(con, tsv_gz):
    """(Re)build colmap from the crosswalk: Fungi backbone usages only, mapped rows
    (col:ID non-empty) only; accepted id/name resolved as above. ~320k rows."""
    csv.field_size_limit(sys.maxsize)
    con.execute("""create or replace table colmap(
        gbif_id varchar, rank varchar, gbif_name varchar,
        col_id varchar, col_status varchar, col_accepted_id varchar, col_accepted_name varchar,
        family varchar, "order" varchar, class varchar, phylum varchar, genus varchar)""")
    n, batch = 0, []
    with gzip.open(tsv_gz, "rt", encoding="utf-8", newline="") as f:
        rd = csv.DictReader(f, delimiter="\t")
        missing = set(TSV_FIELDS.values()) - set(rd.fieldnames or ())
        if missing:
            raise PublicTaxaError(f"{tsv_gz}: crosswalk lacks expected columns {sorted(missing)}")
        for row in rd:
            if row.get("gbif:kingdom") != "Fungi" or not row.get("col:ID"):
                continue                                   # non-Fungi, or no COL match at all
            acc_id = row["col:acceptedID"] or row["col:ID"]
            acc_name = row["col:acceptedScientificName"] or row["col:scientificName"]
            batch.append(tuple(row[TSV_FIELDS[k]] if k not in ("col_accepted_id", "col_accepted_name")
                               else (acc_id if k == "col_accepted_id" else acc_name)
                               for k in ("gbif_id", "rank", "gbif_name", "col_id", "col_status",
                                         "col_accepted_id", "col_accepted_name", "family",
                                         "order", "class", "phylum", "genus")))
            if len(batch) >= 50_000:
                con.executemany("insert into colmap values (" + ",".join(["?"] * 12) + ")", batch)
                n += len(batch)
                batch = []
    if batch:
        con.executemany("insert into colmap values (" + ",".join(["?"] * 12) + ")", batch)
        n += len(batch)
    print(f"colmap: {n} mapped Fungi backbone usages")
    return n


def _has_species_key(con, t):
    cols = {r[0] for r in con.execute(
        f"select column_name from information_schema.columns where table_name='{t}'").fetchall()}
    return "speciesKey" in cols, cols


def apply(con, tables=TABLES):
    """Add/refresh the col_* columns of every speciesKey table by joining colmap on
    speciesKey::varchar = gbif_id. Unmapped keys keep null col_* values."""
    for t in tables:
        has, _ = _has_species_key(con, t)
        if not has:
            continue
        con.execute(f"""create or replace table {t} as
            select src.*, k.col_key, k.col_name, k.col_family, k.col_order,
                   k.col_class, k.col_phylum, k.col_genus
            from {t} src left join (
                select gbif_id, col_accepted_id as col_key, col_accepted_name as col_name,
                       family as col_family, "order" as col_order, class as col_class,
                       phylum as col_phylum, genus as col_genus
                from (select *, row_number() over (partition by gbif_id order by col_id) rn
                      from colmap) where rn = 1) k
            on src.speciesKey::varchar = k.gbif_id""")
        mapped = con.execute(f"select count(col_key) from {t}").fetchone()[0]
        print(f"{t}: col_key set on {mapped} of {con.execute(f'select count(*) from {t}').fetchone()[0]} rows")


def ensure(con, tables=TABLES):
    """Add any missing col_* columns as null (no join), so static SQL can reference
    them uniformly on legacy/fixture databases too."""
    names = {r[0] for r in con.execute("select table_name from information_schema.tables").fetchall()}
    for t in (t for t in tables if t in names):
        _, cols = _has_species_key(con, t)
        for c in (x for x in COL_COLUMNS if x not in cols):
            con.execute(f"alter table {t} add column {c} varchar")


def keyed(con, t="jp"):
    """True when the table actually carries COL keys (a --col-map build): the gate
    then requires col_mask_map.csv. A null-only column (ensure() on a legacy DB)
    keeps the legacy gate."""
    _, cols = _has_species_key(con, t)
    return "col_key" in cols and con.execute(
        f"select count(*) from {t} where col_key is not null limit 1").fetchone()[0] > 0


def exprs(t, cols):
    """Guarded SQL select expressions for COL XR identity and grouping. Every column
    reference is header-guarded, so tables without the col_* columns (fixture DBs,
    legacy builds) fall back to the backbone identity unchanged."""
    g = lambda n: f"{t}.{n}" if n in cols else "null"
    return {"sp_key": f"coalesce({g('col_key')}, 'L' || {g('speciesKey')}::varchar)",
            "col_key": g("col_key"),
            "sp_name": f"coalesce({g('col_name')}, {g('species')})",
            "family": f"coalesce({g('col_family')}, {g('family')})",
            "order": f'coalesce({g("col_order")}, {g("order")})',
            "class": f"coalesce({g('col_class')}, {g('class')})",
            "phylum": f"coalesce({g('col_phylum')}, {g('phylum')})",
            "genus": f"coalesce({g('col_genus')}, {g('genus')})"}


def emit_mask_map(con, gate, out):
    """data/reference/col_mask_map.csv: source,value,col_key,col_accepted_name for
    every legacy key and every mask name/alias the gate loads. Fail closed:
    PublicTaxaError naming each key/name with no COL accepted match (spec §0b STOP)."""
    rows, misses = [], []
    keys = {r[0]: (r[1], r[2]) for r in con.execute(
        "select gbif_id, col_accepted_id, col_accepted_name from colmap").fetchall()}
    for k in sorted(gate.keys):
        if k in keys:
            rows.append(("key", k, *keys[k]))
        else:
            misses.append(f"key {k}")
    for name in sorted(gate.masked_names | gate.ambiguous_names):
        hit = con.execute("""select distinct col_accepted_id, col_accepted_name from colmap
                             where lower(gbif_name) = ? or lower(col_accepted_name) = ?""",
                          (name, name)).fetchall()
        if not hit:
            misses.append(f"name {name}")
        elif len({h[0] for h in hit}) > 1:
            misses.append(f"name {name} (ambiguous: {len(hit)} accepted taxa)")
        else:
            rows.append(("name", name, hit[0][0], hit[0][1]))
    if misses:
        raise PublicTaxaError(
            f"col_mask_map: {len(misses)} gate entr{'y is' if len(misses) == 1 else 'ies are'} "
            f"without a COL accepted match: {', '.join(misses[:10])}"
            + (" ..." if len(misses) > 10 else ""))
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow([f"# Generated by pipeline/col_xr.py emit_mask_map from {URL.rsplit('/', 1)[-1]} "
                    f"(COL 26.5 XR); regenerated after every crosswalk or mask review (TASK-018)."])
        w.writerow(["source", "value", "col_key", "col_accepted_name"])
        w.writerows(rows)
    print(f"{out}: {len(rows)} gate keys/names resolved to COL accepted taxa")
    return rows


def diff_report(con, old_gate, new_gate, out):
    """The §0b diff report (local artifact, gitignored — per-species names stay out
    of the repo): merged, renamed, unmapped, public species and masked counts
    before/after the re-keying."""
    rows = con.execute("""select speciesKey::varchar, coalesce(col_key, ''), species, genus,
                                 taxonRank, coalesce(col_name, '') from jp
                          where speciesKey is not null""").fetchall()
    by_species = {}                                       # legacy key -> (name, col_key, col_name)
    for key, colk, sp, gen, rank, coln in rows:
        by_species.setdefault(key, (sp, colk, coln))
    merged = {}
    renamed = []
    unmapped_records = 0
    for key, (sp, colk, coln) in sorted(by_species.items()):
        if not colk:
            unmapped_records += 1
            continue
        merged.setdefault(colk, []).append((key, sp))
        if coln and coln != sp:
            renamed.append((sp, coln, key))
    pub_before, pub_after = set(), set()
    masked_before, masked_after = set(), set()
    for key, colk, sp, gen, rank, coln in rows:
        if old_gate.is_public(species=sp, species_key=key, genus=gen, taxon_rank=rank):
            pub_before.add(key)
        else:
            masked_before.add(key)
        if new_gate.is_public(species=sp, species_key=key, genus=gen, taxon_rank=rank, col_key=colk or None):
            pub_after.add(colk or f"L{key}")
        else:
            masked_after.add(colk or f"L{key}")
    with open(out, "w", encoding="utf-8") as f:
        f.write(f"# COL XR re-keying diff ({URL.rsplit('/', 1)[-1]}, COL 26.5 XR)\n\n"
                f"Legacy jp species with a speciesKey: {len(by_species)}\n\n")
        f.write(f"## Merged: {sum(1 for v in merged.values() if len(v) > 1)} COL ids lump "
                f"{sum(len(v) for v in merged.values() if len(v) > 1)} legacy species\n\n")
        for colk, keys in sorted(merged.items(), key=lambda kv: (-len(kv[1]), kv[0])):
            if len(keys) > 1:
                f.write(f"- {' + '.join(f'{k} ({n})' for k, n in keys)} -> {colk}\n")
        f.write(f"\n## Renamed: {len(renamed)} species (backbone name != COL accepted name)\n\n")
        for sp, coln, key in sorted(renamed):
            f.write(f"- {key} {sp} -> {coln}\n")
        unmapped_species = [k for k, (_, colk, _) in by_species.items() if not colk]
        f.write(f"\n## Unmapped: {len(unmapped_species)} species with speciesKey but no COL id, "
                f"{unmapped_records} of them counted as species above\n\n")
        f.write(f"\n## Public species: before {len(pub_before)} -> after {len(pub_after)}\n")
        f.write(f"\n## Masked species (distinct): before {len(masked_before)} -> after {len(masked_after)}\n")
    print(f"{out}: merged {sum(1 for v in merged.values() if len(v) > 1)}, renamed {len(renamed)}, "
          f"unmapped {len(unmapped_species)}; public {len(pub_before)} -> {len(pub_after)}; "
          f"masked {len(masked_before)} -> {len(masked_after)}")
    return {"merged": sum(1 for v in merged.values() if len(v) > 1), "renamed": len(renamed),
            "unmapped": len(unmapped_species), "public_before": len(pub_before),
            "public_after": len(pub_after), "masked_before": len(masked_before),
            "masked_after": len(masked_after)}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Re-key the pipeline tables to COL XR (TASK-018).")
    ap.add_argument("--db", default="data/shroomhunt.duckdb")
    ap.add_argument("--map", default="data/raw/col/gbif-col265.tsv.gz", help="the crosswalk TSV.gz")
    ap.add_argument("--mask-map", default="data/reference/col_mask_map.csv")
    ap.add_argument("--diff", help="also write the §0b diff report here")
    ap.add_argument("--mask", default="data/reference/taxon_mask.csv")
    ap.add_argument("--coverage", default="data/reference/redlist_coverage.csv")
    ap.add_argument("--exceptions", default="data/reference/match_exceptions.csv")
    ap.add_argument("--redlist", default="data/raw/redlist/redlist_fungi_jp.json")
    a = ap.parse_args(argv)
    import duckdb
    con = duckdb.connect(a.db)
    load_map(con, a.map)
    apply(con)
    old_gate = load_gate(mask=a.mask, coverage=a.coverage, exceptions=a.exceptions, redlist=a.redlist)
    emit_mask_map(con, old_gate, a.mask_map)
    if a.diff:
        new_gate = load_gate(mask=a.mask, coverage=a.coverage, exceptions=a.exceptions,
                             redlist=a.redlist, col_mask=a.mask_map)
        diff_report(con, old_gate, new_gate, a.diff)
    con.close()


if __name__ == "__main__":
    main()
