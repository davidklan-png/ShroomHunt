# ShroomHunt

Research tool for documenting Japan's fungal diversity and pointing fieldwork at likely native, endemic or undescribed taxa.
Start with **docs/architecture-decisions.md**.

## Folder layout
```
ShroomHunt/
├── app/index.html                   current web app (published as "Forage Season Finder")
├── data/
│   ├── raw/                         downloads go here: never edited, never committed
│   │   ├── mushroomobserver/        ← PUT THE MUSHROOM OBSERVER CSVs HERE
│   │   ├── gbif/                    ← filled by pipeline/gbif_request.py
│   │   └── inaturalist/             (optional, see docs/sources.md)
│   ├── reference/species_traits.csv the curated layer: edibility, lookalikes, habitat (hand-maintained)
│   └── processed/seasonality.json   generated: species → map cell / country → 12 monthly counts
├── pipeline/
│   ├── gbif_request.py              downloads all Japanese fungus records + a neighbouring-country species list
│   ├── inspect_raw.py               prints the columns of every raw file → docs/raw_schema.md
│   └── build_seasonality.py         (foraging version: to be replaced by the research database build)
└── docs/sources.md                  every data source considered, with licence and verdict
```

## Steps (from Terminal, in this folder)
1. **Mushroom Observer** (optional now, mostly North American): download these into `data/raw/mushroomobserver/`:
   - https://mushroomobserver.org/observations.csv
   - https://mushroomobserver.org/names.csv
   - https://mushroomobserver.org/locations.csv

   ```
   cd data/raw/mushroomobserver
   for f in observations names locations; do curl -LO https://mushroomobserver.org/$f.csv; done
   cd ../../..
   ```
2. **GBIF: do this first.** It includes S-Net museum specimens, iNaturalist research-grade sightings and DNA-derived records: create a free account at gbif.org, then
   ```
   export GBIF_USER=... GBIF_PWD=... GBIF_EMAIL=...
   python3 pipeline/gbif_request.py
   ```
   It can take from minutes to a few hours. GBIF emails you when it's ready, and the script keeps waiting.
3. `python3 pipeline/inspect_raw.py`, then tell Claude it's done. The Mushroom Observer loader gets finished once its real columns are known.
4. `python3 pipeline/build_seasonality.py`

Needs only Python 3. No packages to install.
