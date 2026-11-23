# ShroomHunt: species pages, external sources and the records block (2026-11-22)

The agent prompt for this work is `docs/species-pages-agent-prompt.md`.

## David's decisions (2026-11-22)
- **The pipeline moves to the Catalogue of Life eXtended Release (COL XR) taxonomy** (§0b).
- **Toxic (⚠, MHLW) and endemic (固有, Hosoya et al. 2016) markers are approved** for the species lists and details (§5). The endemic marker always carries its citation and "as of 2016", shown next to our own "recorded in neighbouring East Asia" check.

## 0. Fix first: two upstream changes
**0a. The masking list is out of date.** The pipeline masks against the **2020** Japan fungal Red List (GBIF checklist 10.15468/kmv4xm). The Ministry of the Environment published the **5th Red List / Red Data Book for fungi in March 2025**:
- Red List: https://www.env.go.jp/nature/kisho/5th-rl-2025-book/05-5thredlist-fungi.pdf
- Red Data Book: https://www.env.go.jp/nature/kisho/5th-rl-2025-book/10-5threddatabook-fungi.pdf (CC BY-NC-ND 4.0)

Switch the masking input to the 2025 list (fail-closed) before species pages ship. Report which species were added or removed compared with 2020. Species-level masking only.

**0b. GBIF taxonomy: DECIDED, migrate to COL XR.**
- GBIF.org now uses COL XR as its default taxonomy; the legacy GBIF Backbone build is discontinued.
- Taxon pages are `gbif.org/taxon/<alphanumeric COL id>`; old `/species/<key>` links redirect.
- Downloads can carry both legacy and COL XR keys. Keep the legacy key as a secondary field, because Wikidata P846 still uses legacy keys.
- Migration steps:
  1. Re-key all records to COL XR.
  2. Re-match `taxon_mask.csv`, its aliases and the Red List onto COL XR, failing closed.
  3. Write a diff report: species merged, split or renamed; the change in public species count; the change in masked counts (counts only).
- The agent publishes without waiting for David **unless** the masked-species count drops, any mask row goes unmatched, or the public species count changes by more than 5%. In those cases it stops and asks.

## 1. What a species page shows
Species details live inside the dashboard, deep-linkable at `#species-<key>` (COL XR key), built from pipeline data and passed through `public_taxa.py`. **Masked species get no details, no link and no search hit.**

**Attributes from our own data:**
- Scientific name, Japanese name (和名), family / order / class, kind of fungus
- Record count in Japan by evidence tier
- Months recorded: fruiting evidence only, with MATERIAL_SAMPLE excluded
- Areas and islands where recorded (area level only), elevation band, host-plant genera
- Recorded in neighbouring East Asia (yes/no), and the areas where it "could be found here"
- First and last record decade

**Not shown:** edibility. A toxic warning appears only through an MHLW link, plus a fixed "not for deciding edibility" line.

## 2. External sources: what we use and how

| Use | Source | Licence / terms | How |
|---|---|---|---|
| Name authority | GBIF / COL XR; Japanese names checked against Katumoto, K. 2010. *日本産菌類集覧*. Mycological Society of Japan, Kanto Branch | Print book, cite only | Cite in Data and Method |
| Description | Japanese Wikipedia (English as fallback), through Wikidata (P846, legacy GBIF key) | CC BY-SA 4.0 | Link only |
| Photo | Wikimedia Commons (Wikidata P18) | Per image (CC0 / CC BY / CC BY-SA only) | Thumbnail with author, licence and file-page link |
| Toxic marker | MHLW 自然毒のリスクプロファイル: https://www.mhlw.go.jp/stf/seisakunitsuite/bunya/kenkou_iryou/shokuhin/syokuchu/poison/index.html | Government site; confirm terms before quoting | ⚠ marker + link, from a curated species → URL table |
| Endemic marker | Hosoya T., Uzuhashi S., Hosaka K., Kudo S. 2016. 日本産固有菌類目録の作成に向けて. *Trans. Mycol. Soc. Japan* 57(2): 77–84. https://agriknowledge.affrc.go.jp/RN/2010910274.pdf | Cite every time | 固有 marker "listed as endemic, Hosoya et al. 2016", next to our East Asia check. A courtesy note to the Kahaku authors is a good opening for the collaboration |
| Literature | *Mycoscience* (J-STAGE): https://www.jstage.jst.go.jp/browse/mycosci/-char/en | Per article (often CC BY-NC-ND 4.0) | DOI links |
| Further reading (North America) | MushroomExpert: https://www.mushroomexpert.com/ | All rights reserved | Link only, labelled "North America"; only if listed in https://www.mushroomexpert.com/major_groups.html |
| Specimens (later) | Kahaku specimen database / S-Net: https://web.kahaku.go.jp/research/specimen | Unconfirmed | Link at most; photos only through a partnership |

**Not used:** prefectural Red Data Books, and photos attached to GBIF records.

## 3. Decisions
1. Pipeline on COL XR keys (decided).
2. Toxic and endemic markers (decided).
3. Photos: Wikimedia Commons only for now.
4. No edibility.
5. External content is links, not copied text.
6. Species data ships as one file per genus plus a small index.

## 4. The records block: root cause and redesign
**Root cause (checked on the live site): CSS.**
- `.fd-area { display: grid }` and `.fd-btn { display: inline-flex; min-width/height: 44px }` override the `[hidden]` attribute.
- So the area card shows on the main page with empty tables, and the hidden "Show more" buttons appear as empty 44×44 boxes with a hover style.
- Fix: `[hidden] { display: none !important; }` scoped to the dashboard, plus a regression test.

**Main page after the fix:** a single collapsed line under the map, "Species on the map (N) ▸". Expanded, it lists public species for the current month filter, ranked by area count, top 50, plus "Show more" and the existing name search. The map caption gains one sentence pointing to the area view. "Could be found here" appears only in the area view.

## 5. External data in the lists, using little space
- **No new columns.**
- The species name opens an inline details row, about 120 px tall:
  - a 96 px thumbnail with credit
  - one line of key facts
  - one row of links: Wikipedia · GBIF · MHLW · Literature (n) · MushroomExpert (NA)
  - the line "Not for deciding whether a mushroom is edible."
- **Markers after the name, only when true:** ⚠ (toxic: MHLW risk profile) and 固有 (listed as endemic, Hosoya et al. 2016). Each is 14 px inline, with an aria-label and a tooltip.
- A ↗ icon links to `https://www.gbif.org/taxon/<COL id>`. Fallback: `/species/<legacyKey>`, which redirects. A name search only when there is no key, with its URL pattern verified in a browser and kept in one constant.
- `#species-<key>` opens the same details as a compact card.
- The visitor's browser makes no API calls; only Commons thumbnail images load.

## 6. Data and Method section (contents)
1. Data: each GBIF download with its DOI and record count; the snapshot date; the taxonomy (COL XR release); Mushroom Observer via GBIF.
2. Method: the record is the unit, evidence tiers, deduplication, areas, "could be found here" parameters (800 km, 250 km decay, 1.5° latitude), kind classes, fruiting-only months.
3. Protection: no record-level points; species-level masking against the 2025 Red List and the commercial list.
4. Caveats: missing coordinates, sparse habitat data, DNA-sampling bias on Okinawa Island, Mushroom Observer data mostly from the US.
5. Sources and attribution: the §2 table, including what the ⚠ and 固有 markers mean and where they come from.
6. Licence and citation: CC BY-NC 4.0, how to cite, GBIF's citation guidance.

## 7. Order
1. The `[hidden]` CSS hotfix.
2. Prerequisites: synonym masking (code review item #1), fruiting-only months (#3).
3. COL XR migration, then the 2025 Red List masking.
4. Key-based GBIF taxon links.
5. Species bundles and enrichment (coverage report first).
6. Inline details, ⚠ / 固有 markers, the "Species on the map" disclosure.
7. Data and Method section.
