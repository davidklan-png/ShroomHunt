#!/usr/bin/env python3
"""Print the columns and first rows of every raw data file, and write docs/raw_schema.md.
Run after dropping files into data/raw/:  python3 pipeline/inspect_raw.py"""
import csv, gzip, io, os, zipfile, glob, sys
csv.field_size_limit(sys.maxsize)
out = ["# Raw file schemas (generated)\n"]
def peek(name, fh):
    head = fh.read(200_000); text = head.decode("utf-8", "replace")
    try: dialect = csv.Sniffer().sniff(text.split("\n", 1)[0], delimiters="\t,;|")
    except csv.Error: dialect = csv.excel_tab
    rows = list(csv.reader(io.StringIO(text), dialect))[:4]
    d = repr(dialect.delimiter)
    out.append(f"## {name}\n- delimiter: {d}\n- columns: {', '.join(rows[0]) if rows else '(empty)'}\n")
    for r in rows[1:]: out.append("    " + " | ".join(c[:40] for c in r))
    out.append("")
for p in sorted(glob.glob("data/raw/**/*", recursive=True)):
    if os.path.isdir(p): continue
    if p.endswith(".zip"):
        with zipfile.ZipFile(p) as z:
            for n in z.namelist():
                if n.endswith((".csv", ".tsv", ".txt")) or "." not in n:
                    with z.open(n) as fh: peek(f"{p} :: {n}", fh)
    elif p.endswith(".gz"):
        with gzip.open(p, "rb") as fh: peek(f"{p} ({os.path.getsize(p)/1e6:.1f} MB gz)", fh)
    elif p.endswith((".csv", ".tsv", ".txt")):
        with open(p, "rb") as fh: peek(f"{p} ({os.path.getsize(p)/1e6:.1f} MB)", fh)
os.makedirs("docs", exist_ok=True)
open("docs/raw_schema.md", "w").write("\n".join(out))
print("\n".join(out)); print("Wrote docs/raw_schema.md")
