#!/usr/bin/env python3
"""Download Japan's fungal Red List (GBIF checklist "Endangered species list of Fungi in Japan 2020",
dataset 9bac0c5b-7161-409b-ab96-c3bf3f7d7ac1) and resolve each name to a GBIF backbone key.
Writes data/raw/redlist/redlist_fungi_jp.json. No account needed. Used to mask locations (decision D6)."""
import json, os, time, urllib.request, urllib.parse

DATASET = "9bac0c5b-7161-409b-ab96-c3bf3f7d7ac1"
API = "https://api.gbif.org/v1"

def get(path, **q):
    url = f"{API}/{path}?{urllib.parse.urlencode(q)}"
    with urllib.request.urlopen(url, timeout=60) as r: return json.load(r)

def main():
    rows, offset = [], 0
    while True:
        page = get("species/search", datasetKey=DATASET, limit=500, offset=offset)
        rows += page["results"]; offset += 500
        if page.get("endOfRecords", True): break
    out = []
    for r in rows:
        if r.get("rank") not in ("SPECIES", "VARIETY", "SUBSPECIES", "FORM"): continue
        name = r.get("canonicalName") or r.get("scientificName")
        m = get("species/match", name=name, kingdom="Fungi")
        out.append({"name": name, "status": r.get("threatStatuses") or r.get("remarks"),
                    "speciesKey": m.get("speciesKey"), "matchType": m.get("matchType")})
        time.sleep(0.1)
    os.makedirs("data/raw/redlist", exist_ok=True)
    json.dump({"dataset": DATASET, "doi": "10.15468/kmv4xm", "fetched": time.strftime("%Y-%m-%d"),
               "species": out}, open("data/raw/redlist/redlist_fungi_jp.json", "w"), ensure_ascii=False, indent=1)
    print(len(out), "Red List names,", sum(1 for x in out if x["speciesKey"]), "matched to GBIF keys")

if __name__ == "__main__": main()
