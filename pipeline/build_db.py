#!/usr/bin/env python3
"""Load downloaded GBIF files into one DuckDB database (data/shroomhunt.duckdb).
Usage: python3 pipeline/build_db.py <japan.csv> <neighbours.csv> <mushroomobserver.csv>
(the CSVs are inside the release zips). Needs: pip install duckdb"""
import sys, duckdb
jp, nb, mo = sys.argv[1:4]
c = duckdb.connect("data/shroomhunt.duckdb")
r = lambda f: f"read_csv('{f}', delim='\\t', header=true, quote='', all_varchar=true, ignore_errors=true)"
for t, f in (("jp", jp), ("nb", nb), ("mo", mo)):
    c.execute(f"create or replace table {t} as select * from {r(f)}")
    print(t, c.execute(f"select count(*) from {t}").fetchone()[0], "rows")
