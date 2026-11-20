#!/usr/bin/env python3
"""Join raw sightings to the curated species list and write data/processed/seasonality.json:
  {species: {"cells": {"<lat>,<lon>": [12 monthly counts]}, "countries": {"JP": [12]}}}
The app reads this to show real fruiting months for wherever the user is.

Sources handled: GBIF SQL download (data/raw/gbif/*.zip). Mushroom Observer is wired in once
inspect_raw.py has shown its real column names (see load_mushroomobserver)."""
import csv, glob, io, json, os, re, sys, zipfile
csv.field_size_limit(sys.maxsize)

def match_keys(sci):
    """'Boletus edulis group' -> species 'boletus edulis'; 'Morchella spp.' -> genus 'morchella';
    'Amanita virosa / bisporigera' -> species 'amanita virosa' and 'amanita bisporigera'."""
    parts = [p.replace("group", "").split() for p in sci.split("/")]
    genus = parts[0][0].lower()
    if len(parts[0]) == 1 or parts[0][1] == "spp.": return [("genus", genus)]
    keys = [("species", f"{genus} {parts[0][1].lower()}")]
    for w in parts[1:]:  # later parts are bare epithets ('bisporigera') or 'pulmonarius'
        if w: keys.append(("species", f"{genus} {w[-1].lower()}"))
    return keys

def load_traits():
    rows = list(csv.DictReader(open("data/reference/species_traits.csv", encoding="utf-8")))
    sp, gen = {}, {}
    for r in rows:
        for kind, k in match_keys(r["scientific_name"]):
            (sp if kind == "species" else gen)[k] = r["common_name"]
    return rows, sp, gen

def add(acc, name, cc, lat, lon, month, n):
    a = acc.setdefault(name, {"cells": {}, "countries": {}})
    for bucket, key in (("cells", f"{lat},{lon}"), ("countries", cc or "??")):
        arr = a[bucket].setdefault(key, [0] * 12); arr[month - 1] += n

def load_gbif(acc, sp, gen):
    files = glob.glob("data/raw/gbif/*.zip")
    for z in files:
        with zipfile.ZipFile(z) as zf:
            for member in zf.namelist():
                with zf.open(member) as fh:
                    rd = csv.DictReader(io.TextIOWrapper(fh, "utf-8"), delimiter="\t")
                    for r in rd:
                        r = {k.lower(): v for k, v in r.items()}
                        name = (r.get("species") or "").lower()
                        hit = sp.get(name) or gen.get(name.split(" ")[0])
                        if not hit: continue
                        try: m = int(r["month"]); n = int(r.get("n") or r.get("count(*)") or 1)
                        except (KeyError, ValueError): continue
                        add(acc, hit, r.get("countrycode"), int(float(r["lat"])), int(float(r["lon"])), m, n)
    return len(files)

def load_mushroomobserver(acc, sp, gen):
    """TODO after `python3 pipeline/inspect_raw.py`: map MO's columns (name id -> names.csv text,
    location id -> locations.csv bounding box, observation date, vote/confidence) and call add()."""
    return 0

if __name__ == "__main__":
    os.chdir(os.path.dirname(os.path.abspath(__file__)) + "/..")
    traits, sp, gen = load_traits(); acc = {}
    g = load_gbif(acc, sp, gen); m = load_mushroomobserver(acc, sp, gen)
    if not acc: sys.exit("No sightings matched yet: put downloads in data/raw/ first (see README.md).")
    os.makedirs("data/processed", exist_ok=True)
    json.dump(acc, open("data/processed/seasonality.json", "w"), separators=(",", ":"))
    print(f"{g} GBIF file(s), {m} MO file(s) -> {len(acc)}/{len(traits)} species with sightings")
    for k, v in sorted(acc.items()): print(f"  {k:45s} {sum(map(sum, v['countries'].values())):>9,} sightings")
