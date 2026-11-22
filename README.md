# ShroomHunt

Research tool for documenting Japan's fungal diversity and pointing fieldwork at likely native, endemic or undescribed taxa. Start with **docs/architecture-decisions.md**; the map work is specified in **docs/map-improvement-plan.md**.

The published dashboard lives in the Bio_HP site (kinokoholic.com). This repo builds the data bundle under a shared protection gate (`pipeline/public_taxa.py`) and ships it there.

## Folder layout
```
ShroomHunt/
├── app/index.html       legacy foraging page ("Forage Season Finder") — superseded, kept for reference
├── data/
│   ├── raw/             downloads: never edited, never committed (gitignored)
│   ├── reference/       hand-maintained curated layer: taxon_mask.csv (species-level
│   │                    protection list, aliases included), taxon_names.csv (reviewed
│   │                    genus-search table), islands.csv, match_exceptions.csv
│   └── processed/       generated bundles (staging → Bio_HP)
├── pipeline/            duckdb build: build_db → build_dashboard_data → build_areas →
│                        build_mesh_research; public_taxa.py is the protection gate every
│                        output passes; validate_dashboard_publish.py gates each publish
├── analysis/            SQL steps feeding the builders
├── tests/               stdlib unittest suite (fixtures only — no raw data needed)
└── docs/                architecture decisions, map plan, sources
```

## Running

```
/opt/homebrew/bin/python3.12 -m venv .venv && .venv/bin/pip install duckdb==2.0.1
.venv/bin/python -m unittest discover -s tests -p 'test_*.py'    # what CI and verify.sh run
```

Data refreshes run on GitHub Actions: `.github/workflows/fetch-data.yml` (GBIF download → release assets) then `publish-dashboard.yml` (build → validate → Bio_HP data PR). `tests.yml` runs the suite on every push/PR. Manual GBIF download: `pipeline/gbif_request.py` (needs GBIF_USER, GBIF_PWD, GBIF_EMAIL).

Mushroom Observer is optional and low priority (decision D10); the loader stays in the repo.
