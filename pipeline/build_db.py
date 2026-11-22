#!/usr/bin/env python3
"""Load downloaded GBIF files into one DuckDB database.

Usage: python3 pipeline/build_db.py --japan J.csv --neighbours N.csv --mo M.csv
                                    [--japan-dwca occurrence.txt] [--eastasia E.csv]
                                    [--col-map data/raw/col/gbif-col265.tsv.gz] [--db data/shroomhunt.duckdb]
(the CSV/TSV files are inside the release zips). Needs: pip install duckdb
Tables: jp, nb, mo, and optionally jp_extra (gbifID, habitat from the DwC-A) and ea (East Asia cells).
--col-map (TASK-018) loads the COL XR crosswalk into colmap and re-keys every speciesKey
table with the col_* columns (pipeline/col_xr.py); without it the build stays on legacy keys."""
import argparse, duckdb

ap = argparse.ArgumentParser()
ap.add_argument("--japan", required=True)
ap.add_argument("--neighbours", required=True)
ap.add_argument("--mo", required=True)
ap.add_argument("--japan-dwca")
ap.add_argument("--eastasia")
ap.add_argument("--col-map")
ap.add_argument("--db", default="data/shroomhunt.duckdb")
a = ap.parse_args()

c = duckdb.connect(a.db)
r = lambda f: f"read_csv('{f}', delim='\\t', header=true, quote='', all_varchar=true, ignore_errors=true)"
for t, f in (("jp", a.japan), ("nb", a.neighbours), ("mo", a.mo)):
    c.execute(f"create or replace table {t} as select * from {r(f)}")
    print(t, c.execute(f"select count(*) from {t}").fetchone()[0], "rows")
if a.col_map:                      # TASK-018: COL XR re-keying of every speciesKey table
    from col_xr import apply, load_map
    load_map(c, a.col_map)
    apply(c)
if a.japan_dwca:
    # habitat text, host / associated plants and elevation from the DwC-A, plus the
    # source-precision fields build_areas.py needs for spatial eligibility (§7/TASK-005):
    # any non-empty dataGeneralizations/informationWithheld excludes a record from spatial
    # outputs. Every field is header-guarded: an archive without a column gets a null
    # column, never a guess (TASK-006 extends the guard to habitat/associatedTaxa/elevation).
    # Biogeographic realms and placeholders are not habitats, so they are dropped.
    with open(a.japan_dwca) as f:
        dwca_cols = set(f.readline().rstrip("\r\n").split("\t"))
    dwca = lambda name: f"trim({name})" if name in dwca_cols else "null"
    hab, assoc, elev = dwca("habitat"), dwca("associatedTaxa"), dwca("elevation")
    c.execute(f"""create or replace table jp_extra as
        select gbifID,
               case when regexp_matches(lower(coalesce({hab},'')),
                    '^\\s*$|palearctic|indomalayan|nearctic|neotropic|australasian|oriental region|no habitat|^unknown$|^n/?a$')
                    then null else {hab} end as habitat,
               case when regexp_matches(lower(coalesce({assoc},'')), '^\\s*$|undetermined|^unknown')
                    then null else trim(regexp_replace({assoc}, '^(host|substrate|on)\\s*:\\s*', '', 'i')) end as assoc,
               try_cast({elev} as double) as elevation,
               {dwca("dataGeneralizations")} as dataGeneralizations,
               {dwca("informationWithheld")} as informationWithheld
        from {r(a.japan_dwca)}""")
    print("jp_extra", c.execute(
        "select count(*), count(habitat), count(assoc), count(elevation), "
        "count(dataGeneralizations), count(informationWithheld) from jp_extra").fetchone(),
        "(rows, habitat, host/associate, elevation, generalized, withheld)")
if a.eastasia:
    # records -> species x 1-degree cell x month
    c.execute(f"""create or replace table ea as
        select speciesKey, species, family, round(try_cast(decimalLatitude as double)) as lat,
               round(try_cast(decimalLongitude as double)) as lon, try_cast(month as int) as month, count(*) as n
        from {r(a.eastasia)} where speciesKey is not null and species is not null
        group by all""")
    print("ea", c.execute("select count(*), sum(n), count(distinct species) from ea").fetchone(), "(cells, records, species)")
