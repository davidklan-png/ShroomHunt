#!/usr/bin/env python3
"""Load downloaded GBIF files into one DuckDB database.

Usage: python3 pipeline/build_db.py --japan J.csv --neighbours N.csv --mo M.csv
                                    [--japan-dwca occurrence.txt] [--eastasia E.csv] [--db data/shroomhunt.duckdb]
(the CSV/TSV files are inside the release zips). Needs: pip install duckdb
Tables: jp, nb, mo, and optionally jp_extra (gbifID, habitat from the DwC-A) and ea (East Asia cells)."""
import argparse, duckdb

ap = argparse.ArgumentParser()
ap.add_argument("--japan", required=True)
ap.add_argument("--neighbours", required=True)
ap.add_argument("--mo", required=True)
ap.add_argument("--japan-dwca")
ap.add_argument("--eastasia")
ap.add_argument("--db", default="data/shroomhunt.duckdb")
a = ap.parse_args()

c = duckdb.connect(a.db)
r = lambda f: f"read_csv('{f}', delim='\\t', header=true, quote='', all_varchar=true, ignore_errors=true)"
for t, f in (("jp", a.japan), ("nb", a.neighbours), ("mo", a.mo)):
    c.execute(f"create or replace table {t} as select * from {r(f)}")
    print(t, c.execute(f"select count(*) from {t}").fetchone()[0], "rows")
if a.japan_dwca:
    c.execute(f"create or replace table jp_extra as select gbifID, nullif(trim(habitat), '') as habitat from {r(a.japan_dwca)}")
    print("jp_extra", c.execute("select count(*), count(habitat) from jp_extra").fetchone(), "(rows, with habitat)")
if a.eastasia:
    c.execute(f"create or replace table ea as select * from {r(a.eastasia)}")
    print("ea", c.execute("select count(*) from ea").fetchone()[0], "rows; columns:",
          [x[0] for x in c.execute("describe ea").fetchall()])
