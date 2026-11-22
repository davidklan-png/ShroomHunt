# ShroomHunt: architecture decisions (2026-11-21)

**Purpose (changed 2026-11-21):** a research tool to expand the documented fungal diversity of Japan
(edible, inedible and poisonous alike) and to point fieldwork at likely native, endemic or undescribed taxa.
The foraging app ("Forage Season Finder") becomes, at most, a side view.

Status key: **Decided** = applied in code · **Proposed** = needs David's confirmation

---

### D1. A record is the unit, not a count (Decided)
Keep every occurrence record with full provenance, stored as Darwin Core fields: who collected it, when,
where (with uncertainty), what kind of evidence (specimen / photo / DNA), voucher number, and every identification.
- Records identified only to genus or as "sp." are **kept**. For discovery work they're the most valuable records, not noise.
- `gbif_request.py` now pulls every Japanese fungus record GBIF holds, plus a species list for neighbouring countries.
- *Future impact:* storage grows to millions of rows, so expect SQLite/DuckDB instead of JSON files. The app then reads
  query results rather than one bundled file.

### D2. Evidence tiers drive every claim (Decided)
| Tier | Evidence | Can support |
|---|---|---|
| A | Specimen in a public fungarium **plus** a DNA sequence (ITS barcode) | New records for Japan, candidate new species |
| B | Specimen in a public fungarium | Distribution records |
| C | Community-verified photo (iNaturalist research grade, Mushroom Observer consensus) | Phenology, range hints |
| D | Unverified (app identifications, casual records, AI suggestions) | Leads to check only |

*Why it matters:* a new species can't be established from observations. It needs a preserved type specimen
in a public collection, DNA, and a formal published description. The tool's job is to **find where to look and
which records to verify**. It cannot make the discovery itself. AI-generated species lists ("Ask Claude") are tier D and stay out of research outputs.
- *Applied in code (2026-11-22):* every record carries an evidence code (s specimen / o observation /
  d DNA sample); month histograms count fruiting evidence only, so DNA sampling never shows up
  as phenology.

### D3. DNA is part of the core design (Proposed)
- **UNITE** clusters all public fungal ITS sequences into "species hypotheses" (SHs), each with a DOI, including
  taxa that have no Latin name yet. An SH with sequences from Japan but no name = a **candidate undescribed taxon**.
- **GlobalFungi** holds soil and root sequencing samples with GPS coordinates, which show fungi present at a site that
  nobody has seen fruit.
- *Future impact:* this adds a second identity system (SH codes alongside Latin names), so the data model needs a
  taxon table that can hold both from day one.

### D4. Names: GBIF backbone + Japanese names + printed checklists (Proposed)
- Use GBIF taxon keys for joining, and Species Fungorum / MycoBank for the currently accepted nomenclature.
- Add a Japanese-name (和名) table. Much of the local knowledge is published under Japanese names only.
- The baseline checklists are in print (Imazeki & Hongo's illustrated mushroom volumes, 1987–89; Katumoto's 2010 list of Japanese fungi).
  A 2016 assessment of the Imazeki & Hongo names found most of them had **unknown overseas distribution**,
  which shows how large the information gap is.

### D5. Spatial unit: Japan's standard grid mesh (Proposed)
Analyse on the JIS X 0410 地域メッシュ (2nd mesh ≈ 10 km). It's the grid Japanese government biodiversity data uses,
so results line up with Ministry of the Environment layers. Raw coordinates are always kept.
*Replaces* the 1° (~111 km) cells used for the foraging version.

### D6. Protect sensitive locations (Decided)
Mask exact coordinates of Red List fungi (Endangered species list of Fungi in Japan 2020, on GBIF)
and of commercially poached/collected species (`data/reference/taxon_mask.csv`) in anything shared.
S-Net already masks Red List localities before publishing, so the same rule keeps our outputs
consistent with theirs.
- **Species-level only (David, 2026-11-22):** a masked taxon is a species — matched by accepted
  name *and its aliases* (e.g. Ganoderma lingzhi / G. sichuanense for G. lucidum) — never a
  genus. Genus-rank records publish and no genus is excluded from Name search.
- Genus search labels never name a masked species ("matsutake" must not surface Tricholoma
  areas): `taxon_names.csv` carries genus-level terms only.

### D7. The analyses ("discovery engine") (Proposed, in priority order)
1. **Known next door, not in Japan:** fungi recorded in China, Korea, Taiwan or the Russian Far East with no Japanese record.
2. **Stuck identifications:** clusters of genus-only / "sp." Japanese records by mesh and season. These are candidate unknown taxa.
3. **Unnamed DNA clusters:** UNITE SHs with Japanese sequences and no binomial.
4. **Survey gaps:** meshes with few fungus records compared with their forest type and the plant records there. These are fieldwork priorities.
5. **Not seen since:** species with only old Japanese records. These are rediscovery or loss candidates.

### D8. Fieldwork and legal (Proposed)
- Japan does not require government prior informed consent for access to its genetic resources (Nagoya Protocol ABS
  Guidelines, 2017). Landowner permission and park permits (national-park special zones) still apply.
- Exporting specimens or DNA: an official document of acquisition in Japan can be obtained via NITE.
- Deposit vouchers in a public fungarium. National Museum of Nature and Science (TNS) is the natural partner.

### D9. Partners before scale (Proposed)
JBIF / S-Net at the National Museum of Nature and Science (the GBIF Japan node), the Mycological Society of Japan
(日本菌学会), and local mushroom clubs (きのこの会) for verification and specimens. The Biome app reports more than 6M Japanese
observations, but its data isn't openly downloadable as far as we know, so access would need an agreement.

### D10. Mushroom Observer is now low priority (Decided)
It's mostly North American. Keep the CSV loader optional.

### D11. Taxonomy keys: COL XR, legacy backbone key kept (Decided 2026-11-22)
GBIF's legacy Backbone is discontinued and frozen; GBIF.org now defaults to the Catalogue
of Life eXtended Release, and taxon pages are `gbif.org/taxon/<COL id>`. Every public
output is keyed on COL XR (spec `docs/species-pages-spec.md` §0b, TASK-018):
- **Mapping**: the static crosswalk `gbif-col265.tsv.gz` (COL 26.5 XR against the backbone
  of 2023-08-28, https://download.checklistbank.org/col/gbif/gbif-col265.tsv.gz, fetched by
  `pipeline/fetch_col_mapping.py` with a Last-Modified sidecar) loads into `colmap`, and
  `pipeline/col_xr.py apply` re-keys jp/nb/mo/ea: `col_key` is the accepted COL id, with the
  name and the family/order/class/phylum/genus classification of the accepted taxon
  (null keeps the backbone value; unmapped keys keep null `col_key`).
- **Identity**: species identity is `coalesce(col_key, 'L' || speciesKey)` and the display
  name `coalesce(col_name, species)`, so two backbone synonyms lumped under one COL id count
  and list once. The legacy `speciesKey` stays on every record and in the gate: Wikidata P846
  still uses legacy keys.
- **Fail-closed masking**: `data/reference/col_mask_map.csv` (generated by
  `pipeline/col_xr.py emit_mask_map`, reviewed) maps every gate-loaded Red List key and
  mask name/alias to its COL id. A build whose tables carry COL keys — and every
  `validate_dashboard_publish.py` run — refuses to proceed without it; generation raises
  when any entry matches no COL accepted taxon.
