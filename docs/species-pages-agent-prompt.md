# Coding agent prompt: species pages, COL XR migration, records block, Data and Method (2026-11-22)

How to use: tell the coding agent "Read docs/species-pages-agent-prompt.md in the ShroomHunt repo and carry it out", or paste everything below the line. It combines the earlier prompt, its addendum and David's decisions of 2026-11-22.

Companion docs in this repo (ShroomHunt):
- `docs/species-pages-spec.md`: the spec (sources, decisions, design)
- `docs/code-review-2026-11-22.md`: the code review this work builds on

---

Work in two repos: ShroomHunt (data pipeline, GitHub Actions) and Bio_HP (site: kinokoholic.com/kinokomon/). Before starting, read `docs/species-pages-spec.md` (the spec) and `docs/code-review-2026-11-22.md`, both in the ShroomHunt repo.

## David's decisions (2026-11-22)
- **Move the pipeline to the Catalogue of Life eXtended Release (COL XR) taxonomy.**
- **Toxic (⚠, MHLW) and endemic (固有, Hosoya et al. 2016) markers are approved.**
- **Masking is species-level only.** Never mask or exclude a genus.
- **No edibility information anywhere.**

## Non-negotiable rules
- Every public output goes through `public_taxa.py` and the existing validator. Fail closed.
- Masked species get no species details, no list entry, no search hit and no outbound link.
- No record-level locations anywhere. That includes outbound links to GBIF or iNaturalist record pages and photos taken from Japanese records.
- Every external item shown (photo, link, marker) carries its source and, for media, its licence and author. If the licence is unknown, use a link only. Never copy text or images from all-rights-reserved sources (MushroomExpert, prefectural RDBs, Mycoscience figures).
- A toxic warning appears only as the ⚠ marker plus a link to the species' MHLW risk-profile page. Every species details view shows: "Not for deciding whether a mushroom is edible."
- Data contract: change JSON shapes only together with the validator (Python) and the JS readers. Add or extend tests first. One PR per numbered item.
- Use little page space: no new table columns.

## Items, in order

### 1. HOTFIX: empty boxes in the records block (Bio_HP). Separate PR, ship first.
Root cause, confirmed on the live site:
- `.fd-area { display: grid }` and `.fd-btn { display: inline-flex; min-width: 44px; min-height: 44px }` override the `[hidden]` attribute.
- `#fd-area` therefore shows on the main page with an empty title and empty tables.
- The hidden, label-less `#fd-area-more` / `#fd-poss-more` buttons appear as empty 44×44 boxes with a hover style.

Fix:
- Add `[hidden] { display: none !important; }`, scoped to the fungi dashboard (or globally, if `site.css` allows).
- Tests: every `[hidden]` element under the dashboard has computed `display: none`; no button has an empty accessible name.

### 2. Prerequisites (skip any already merged)
- 2a. Synonym masking aliases in `taxon_mask.csv` (code review item 1).
- 2b. Fruiting-only months: exclude `basisOfRecord = MATERIAL_SAMPLE` from area and research month counts, and add a `dominant_source` sampling note (code review item 3).

### 3. COL XR migration (ShroomHunt)
- GBIF.org now uses COL XR as its default taxonomy, and the legacy GBIF Backbone build is discontinued. Taxon pages are `https://www.gbif.org/taxon/<alphanumeric COL id>`; old `/species/<key>` links redirect.
- Read GBIF techdocs "Taxonomy interpretation" (https://techdocs.gbif.org/en/data-processing/taxonomy-interpretation) and the data-blog post "How to migrate from the legacy GBIF Backbone Taxonomy to Catalogue of Life Extended Release" (https://data-blog.gbif.org/post/catalogue-of-life-taxonomic-backbone/).
- Re-download or re-interpret so that every record carries its COL XR taxon key. Keep the legacy backbone key as a secondary field, because Wikidata P846 still uses legacy keys.
- Re-match `taxon_mask.csv`, its aliases and the Red List onto COL XR. Fail closed on any unmatched mask row.
- Diff report in the PR: species merged, split or renamed; the change in public species count; the change in masked counts (counts only, never masked names in public output).
- **Proceed without waiting for sign-off, unless** the masked-species count drops, any mask row or alias fails to match, or the public species count changes by more than 5%. In those cases, stop and ask David.
- Switch the deep-link and bundle key to the COL XR key. Old `#area-` links are unaffected.

### 4. 2025 Red List masking input (ShroomHunt)
- The masking input is the 2020 Red List (GBIF 10.15468/kmv4xm). The Ministry of the Environment published the 5th Red List for fungi in March 2025: https://www.env.go.jp/nature/kisho/5th-rl-2025-book/05-5thredlist-fungi.pdf (Red Data Book: https://www.env.go.jp/nature/kisho/5th-rl-2025-book/10-5threddatabook-fungi.pdf, CC BY-NC-ND 4.0).
- Parse it into `data/reference/` (category, Japanese name, scientific name), matched to COL XR including synonyms.
- Make it the masking input, fail-closed. Keep 2020 as a superset during the transition if that is safer.
- Report the species added or removed compared with 2020 (counts in the PR; names only in a private artifact for David), plus any names that don't match.

### 5. Key-based GBIF links (Bio_HP + bundle field)
Replace every `https://www.gbif.org/species/search?q=<name>` (area species table, could-be-found table, leads table) with a key-based link:
- primary: `https://www.gbif.org/taxon/<COL id>`
- fallback: `https://www.gbif.org/species/<legacyKey>` (redirects)
- last resort, only for names with no key: a GBIF name search. Verify the current search URL pattern in a real browser first (it may be `/taxon/search?q=` or similar), and keep it in a single constant.

Build every URL in one helper function. Tests: no `species/search` strings remain. Add a scheduled CI job (not on every push) that checks a sample of generated URLs resolves (HTTP 200 after redirects).

### 6. Deep dive and coverage report (no UI changes; report in the PR description)
For public species (counts and percentages):
- **Wikidata:** an item found by legacy GBIF key (P846); ja / en Wikipedia sitelinks; a P18 image with an acceptable Commons licence (CC0 / CC BY / CC BY-SA, read from the Commons API extmetadata).
- **MHLW toxic marker:** build a curated CSV (COL key → URL) from https://www.mhlw.go.jp/stf/seisakunitsuite/bunya/kenkou_iryou/shokuhin/syokuchu/poison/index.html. Check the site's terms of use; record whether quoting is allowed or only linking (link only by default).
- **Hosoya endemic marker:** Hosoya T., Uzuhashi S., Hosaka K., Kudo S. 2016. 日本産固有菌類目録の作成に向けて. *Trans. Mycol. Soc. Japan* 57(2): 77–84. https://agriknowledge.affrc.go.jp/RN/2010910274.pdf
  - Extract the list into a reference CSV matched to COL XR, and report unmatched names.
  - For each listed species, compute whether our neighbouring East Asia data records it (DOIs 10.15468/dl.akuuf8 and dl.8xq5m7), and report how many conflict. Those conflicts are expected and shown, not hidden.
- **Mycoscience literature:** investigate the J-STAGE WebAPI and Crossref (journal ISSN 1618-2545) for papers whose title or abstract names a public species or genus. Report the hit rate and the false-positive risk. Links only.
- **MushroomExpert:** fetch the species index once (https://www.mushroomexpert.com/major_groups.html) with a descriptive User-Agent, and cache it. Match species and genus pages, and report coverage. Do not probe guessed URLs.
- **Kahaku specimen database / S-Net** (https://web.kahaku.go.jp/research/specimen): find out whether species-level linking is possible and what the image terms are. Report only, don't integrate.
- **Size estimate** for per-genus species bundles plus an index: total size and expected churn per refresh.

Stop and ask David if any source has unclear terms or coverage below about 20%. Otherwise continue.

### 7. Species bundles and enrichment (ShroomHunt)
Build per-genus species bundles plus an index, generated in "Publish dashboard" and validated. Fields:
- `key` (COL XR), `legacy_key`, scientific name, 和名, family / order / class, kind
- Japan record count by evidence tier; fruiting months
- area ids where recorded (area level only); elevation band; host-plant genera
- East Asia presence (bool); "could be found here" area ids; first and last decade
- `external`: `{ wikidata_qid, wikipedia_ja, wikipedia_en, photo: {thumb_url, file_page, author, licence, licence_url}, mhlw_url, endemic_hosoya2016 (bool), literature: [{doi, title, year}], mushroomexpert_url, mushroomexpert_level: "species"|"genus" }`

Run the enrichment in Actions and cache the results under `data/reference/` with retrieval dates. A failed lookup leaves the field empty and never blocks a publish. Keep the snapshot in `index.json` with per-file hashes, so a refresh doesn't rewrite every file.

### 8. External data in the lists, using little space (Bio_HP)
- **The species name** becomes an in-page button/link that expands an inline details row under that row: one open at a time; Esc or a second click closes it; `aria-expanded` is set; it keeps scroll position. Target height about 120 px:
  - a 96 px Commons thumbnail with a credit line (author · licence · file link), or nothing if there is no image;
  - one line: evidence tiers, a fruiting-months sparkline, the area count, "Recorded in neighbouring East Asia: yes/no";
  - one row of text links, each only if present: Wikipedia (ja, else en) · GBIF · MHLW risk profile · Literature (n) · MushroomExpert (North America);
  - the line "Not for deciding whether a mushroom is edible."
- **Markers after the name, only when true, 14 px inline (never columns):**
  - ⚠ with aria-label and tooltip "Toxic: see the MHLW risk profile" (the link lives in the details row);
  - 固有 with aria-label and tooltip "Listed as endemic in Hosoya et al. 2016". When the details row is open, it shows "Endemic as of 2016 (Hosoya et al.)" next to our own "Recorded in neighbouring East Asia: yes/no".
- **A ↗ icon** at the end of the name cell: the GBIF taxon link from item 5, opening in a new tab.
- **`#species-<key>` deep links** open the same details as a compact card at the top of the lists. Masked or unknown keys show "No public page for this taxon", without saying why.
- **No browser calls** to Wikidata, Commons or GBIF APIs; only Commons thumbnail `<img>` requests.

### 9. Main page after the hotfix (Bio_HP)
- Under the map, add one collapsed `<details>` line: "Species on the map (N)". Expanded, it lists public species for the current month filter, ranked by area count: name + area count + markers, top 50, "Show more", sharing the existing name search. The data comes from the species index (item 7), not a new bundle.
- Add one sentence to the map caption: "Select a square or island to see what's recorded there and what could be found nearby."
- "Could be found here" appears only in the area view.

### 10. Data and Method section (Bio_HP)
Rewrite it, or create it, with these subsections. Links open the original source; DOIs use `https://doi.org/` links; plain language for both researchers and the public.
1. **Data:**
   - each GBIF download with its DOI link and record count: Japan all fungi 10.15468/dl.ng3tup; Darwin Core archive 10.15468/dl.4ybwrt; East Asia species list 10.15468/dl.8xq5m7; East Asia records 10.15468/dl.akuuf8; Mushroom Observer via GBIF 10.15468/dl.pzk484 (update these if the COL XR migration produces new downloads)
   - the snapshot date, read from `index.json`
   - the taxonomy: GBIF / Catalogue of Life eXtended Release, with the release identifier
2. **Method:** record as the unit; evidence tiers; deduplication; areas (28 islands, 7 groups, JIS 1st-mesh tiles); "could be found here" parameters (800 km, 250 km decay, 1.5° latitude); kind-of-fungus classes; fruiting-only months.
3. **Protection:** no record-level points; species-level masking against the Ministry of the Environment 5th Red List (2025, linked) and a commercial-collection list. Explain why, but list no masked names.
4. **Caveats:** missing coordinates (state the current %), sparse habitat data, DNA-sampling bias on Okinawa Island, Mushroom Observer data mostly from the US.
5. **Markers and species-details sources:**
   - ⚠: what it means; the source is MHLW 自然毒のリスクプロファイル (linked)
   - 固有: what it means; the full Hosoya et al. 2016 citation and link; that it reflects 2016 knowledge and is shown next to our own East Asia records
   - Wikidata / Wikipedia (CC BY-SA)
   - Wikimedia Commons (per-image licences)
   - Mycoscience / Mycological Society of Japan (J-STAGE link)
   - MushroomExpert (link; North America)
   - Katumoto 2010, 日本産菌類集覧 (Japanese name authority; print)
6. **Licence and citation:** the combined data is CC BY-NC 4.0 (non-commercial); how to cite the dashboard; GBIF's citation guidance.

### 11. Tests and CI
- **ShroomHunt pytest covering:**
  - the COL XR re-keying (a fixture record with a legacy key maps to its COL key)
  - the 2025 mask, including aliases
  - the gate on species bundles (a masked fixture species produces no bundle entry and no marker)
  - enrichment failures being harmless
  - licence filtering (a non-free Commons image is rejected)
  - the endemic and MHLW reference CSVs joining correctly
- **A pytest workflow** on push and PR, plus a pytest step before building in `publish-dashboard.yml` (code review item 7, if not done).
- **Bio_HP smoke test over the real data files:**
  - no empty interactive elements
  - species lists render
  - details rows render for a sample of keys
  - markers appear only where the bundle flags are true
  - a masked key shows the no-page state
- **Screenshots in every UI PR:** light and dark mode, desktop and 375 px mobile.

## Ask David before
- quoting MHLW text instead of linking
- any use of Kahaku or Kanagawa museum images
- any fallback to photos attached to GBIF records (even from outside Japan)
- any of the item 3 stop conditions

## Deliverables
- PRs in item order (1 → 11). Item 1 ships immediately.
- Each PR has tests, a one-line changelog entry, and screenshots for UI changes.
- The item 3 diff report and the item 6 coverage report go in their PR descriptions.
- Update `docs/architecture-decisions.md` with: the COL XR migration, the 2025 Red List switch, the species-details source policy, and the ⚠ / 固有 marker definitions.
