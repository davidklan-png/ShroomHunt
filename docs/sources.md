# Data sources for ShroomHunt (surveyed 2026-11-21)

## Use: bulk sighting data
| Source | What it gives | Access | Verdict |
|---|---|---|---|
| **GBIF** (gbif.org) | Global hub: aggregates iNaturalist research-grade sightings, Mushroom Observer (check), national fungal databases, herbaria | Free account; SQL download API returns pre-counted tables; each download has a DOI for citation | **Primary source.** One query covers most of the sources below |
| **Mushroom Observer** (mushroomobserver.org) | Community sightings with consensus votes on the species | Nightly CSV dumps (`observations/names/locations.csv`); API is NOT for bulk or AI use | Use the CSVs. Strong in North America. Check whether it's already in GBIF to avoid double counting |
| **iNaturalist Open Data** (AWS `s3://inaturalist-open-data`) | Monthly snapshot: observations, taxa | `aws s3 cp … --no-sign-request`, about 1.6 GB of observations | Only if GBIF's weekly research-grade feed isn't enough. Mostly redundant |
| **MyCoPortal** (mycoportal.org) | 4M+ specimen records, mostly North American fungaria | Darwin Core archives per collection | Herbarium specimens skew to old records. Low priority |

## Regional databases (most already flow into GBIF)
- **Sweden** Artportalen · **Britain & Ireland** Fungal Records Database (FRDBI) · **Denmark** Danish Mycological Society fungal atlas · **Switzerland** SwissFungi · **Norway** Species Observation Service · **Netherlands/EU** observation.org · **Australia** Fungimap / Atlas of Living Australia · **NZ** PDD fungal collection
- **Japan:** coverage in GBIF is mainly museum specimens (National Museum of Nature and Science (TNS), Kanagawa and Gunma prefectural museums), plus iNaturalist. Japan is under-covered by sighting data, so regional detail for Japan will need local sources (prefectural mushroom-poisoning notices, local きのこの会 clubs) entered by hand.

## Community boards: verification, not data
Shroomery, r/mycology, r/foraging, the iNaturalist forum, local mycological society Facebook groups.
Posts are unstructured, often unverified, and forum terms generally forbid scraping. Use them to link out for "get your find checked by people near you", not to harvest data.

## Edibility and safety (the curated layer)
No open dataset reliably covers edibility and lookalikes, so `data/reference/species_traits.csv` stays hand-curated and reviewed.
The 1987 UCI "Mushroom" ML dataset is synthetic (23 species, built from one North American field guide) and is not usable for this.

## Licence notes
- GBIF: each record carries CC0 / CC BY / CC BY-NC. Cite the download DOI. Exclude CC BY-NC records if the app ever becomes commercial.
- Mushroom Observer: the CSV licence isn't stated in the API README. Confirm with them before redistributing derived data publicly.
