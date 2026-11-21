#!/usr/bin/env python3
"""Download the newest release asset for each input the dashboard needs (japan, neighbours,
mushroomobserver, redlist) from this repo's releases into data/raw/. Uses the GitHub REST API via `gh`.
Usage: GH_TOKEN=... python3 pipeline/fetch_latest_release_assets.py owner/repo"""
import json, os, re, subprocess, sys, zipfile

repo = sys.argv[1]
# name pattern -> (folder, required). Exact patterns, because "japan_" would also match "japan_dwca_".
PREFIXES = {"japan_": ("data/raw/gbif", True), "neighbours_": ("data/raw/gbif", True),
            "mushroomobserver_": ("data/raw/gbif", True), "redlist_fungi_jp": ("data/raw/redlist", True),
            "japan_dwca_": ("data/raw/gbif", False), "eastasia_cells_": ("data/raw/gbif", False)}
def matches(name, p):
    return re.match(re.escape(p) + (r"\d" if p.endswith("_") else ""), name) is not None

def gh(*args, binary=False):
    out = subprocess.run(["gh", "api", *args], check=True, capture_output=True)
    return out.stdout if binary else json.loads(out.stdout)

releases = gh(f"repos/{repo}/releases?per_page=100")           # newest first
picked = {}                                                       # prefix -> assets of its newest release
for p in PREFIXES:
    for rel in releases:
        assets = [a for a in rel["assets"] if matches(a["name"], p)]
        if assets:
            picked[p] = [dict(a, release=rel["tag_name"]) for a in assets]
            break
missing = [p for p, (_, req) in PREFIXES.items() if req and p not in picked]
if missing: sys.exit(f"No release asset found for: {missing}")
for p, a in ((p, a) for p, lst in picked.items() for a in lst):
    os.makedirs(PREFIXES[p][0], exist_ok=True)
    path = os.path.join(PREFIXES[p][0], a["name"])
    open(path, "wb").write(gh("-H", "Accept: application/octet-stream", f"repos/{repo}/releases/assets/{a['id']}", binary=True))
    if path.endswith(".zip"):
        with zipfile.ZipFile(path) as z:
            members = [m for m in z.namelist() if p != "japan_dwca_" or m == "occurrence.txt"]   # DwC-A: occurrences only
            z.extractall(os.path.dirname(path) + "/" + p.rstrip("_"), members=members)
    print(f"{a['release']}: {a['name']}")
