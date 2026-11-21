# Codex task: independent options, head-to-head evaluation, and a synthesized implementation plan for the ShroomHunt map

You are a senior front-end and geospatial engineer. **Do not write production code in this task.** Your output is one planning document that a separate coding agent will carry out.

## Links

**Local files (read these)**
- ShroomHunt repo: `/Users/dklan/projects/personal/ShroomHunt`
- This prompt: `/Users/dklan/projects/personal/ShroomHunt/docs/codex-map-review-prompt.md`
- Other reviewer's full options write-up. **Do not open it until Step 3**: `/Users/dklan/projects/personal/ShroomHunt/docs/map-improvement-options.md`
- Existing project decisions: `/Users/dklan/projects/personal/ShroomHunt/docs/architecture-decisions.md`
- Existing source and DOI list: `/Users/dklan/projects/personal/ShroomHunt/docs/sources.md`
- Dashboard data builders: `pipeline/build_dashboard_data.py` (writes `dashboard.json`, including `mesh`), `pipeline/build_areas.py` (writes `areas/index.json` and `areas/<id>.json`), `pipeline/geo_ja.py` (JIS mesh and place-name helpers), `pipeline/build_db.py` (DuckDB build)
- Reference data: `data/reference/islands.csv` (curated island boxes and aliases), `data/reference/sensitive_species.csv` (location-protection list, not yet reviewed)
- Workflows: `.github/workflows/fetch-data.yml` ("Fetch data"), `.github/workflows/publish-dashboard.yml` ("Publish dashboard")

**Repos**
- Pipeline: https://github.com/davidklan-png/ShroomHunt
- Site: https://github.com/davidklan-png/Bio_HP. If there is no local checkout next to ShroomHunt, clone it; work on a scratch branch and do not push.

**Live site and data (read-only)**
- Dashboard: https://kinokoholic.com/kinokomon/
- Okinawa drill-down: https://kinokoholic.com/kinokomon/#area-okinawa-honto
- Map script: https://kinokoholic.com/assets/js/fungi-dashboard.js
- Map styles: https://kinokoholic.com/assets/css/fungi-dashboard.css
- Dashboard data: https://kinokoholic.com/assets/data/shroomhunt/dashboard.json
- Area index: https://kinokoholic.com/assets/data/shroomhunt/areas/index.json

**For David (private pages; Codex cannot open these)**
- Options doc in Claude: https://claude.ai/artifact/CkPqrk5QN9KkriAxrJnALa

## Context

- **Site:** kinokoholic.com/kinokomon/. It is a static Jekyll site on Cloudflare, repo `davidklan-png/Bio_HP`. It hosts a public dashboard of fungal records in Japan.
- **Data pipeline:** repo `davidklan-png/ShroomHunt`. The GitHub Actions workflow "Publish dashboard" rebuilds `assets/data/shroomhunt/dashboard.json` and `assets/data/shroomhunt/areas/*.json`, then opens a PR on Bio_HP.
- **The map:** `assets/js/fungi-dashboard.js`, specifically `setupMap()` plus the helpers `project`, `unproject`, `mapSize`, `zoomBox`, `bucket`, `meshKey` and `areaAt`. Styles are in `assets/css/fungi-dashboard.css` (`.fd-map*`). Tests are in `tests/fungi_dashboard.test.js`.
- **Purpose:** a research tool to point fieldwork at likely native, endemic or undescribed taxa. Genus-only records are the strongest signal of undescribed taxa. The owner (David, a project manager with an infrastructure background) is travelling to Okinawa and will use the map on a phone.
- **Goal of this work:** materially improve the map area of the page, both functionally and visually, without a massive rewrite of the site.

## Hard constraints

1. **Location protection.**
   - Never publish record-level points or anything finer than the JIS 2nd mesh (~10 km).
   - Red List and commercially collected species (`data/reference/sensitive_species.csv` in ShroomHunt, not yet reviewed) stay off the map and out of every list.
2. **No massive rewrite.**
   - The area drill-down, the `#area-<id>` hash routing, the area tables, the EN/JA bilingual labels and the rest of the dashboard must keep working.
   - Changes should be confined to the map and its data.
3. **Static hosting only.** No server code. Data reaches the site only through pipeline PRs.
4. **Licence.** The data is CC BY-NC 4.0. Any basemap, tile source or geodata must allow non-commercial use, and its attribution must be shown.
5. **Theme.** The site is dark-first. Light mode is only half applied, because `styles.css` forces dark tokens. Map colours must work in both themes.
6. **Tests.** Pure helpers stay exported and unit-tested.
7. **Mobile.** The map must work with touch on a phone, possibly on a weak signal.

## Process (follow the order; it exists to stop you anchoring on someone else's answer)

### Step 1: Investigate on your own
Read the Bio_HP map code, CSS, tests and data files, and the ShroomHunt publish workflow plus `pipeline/build_dashboard_data.py`, `pipeline/build_areas.py` and `pipeline/geo_ja.py`. If you can, run the site locally and look at the map at the Japan level and zoomed into Okinawa Island (`#area-okinawa-honto`). Write down:
- what the code and data actually support
- the weaknesses you find, with file and line references
- the data volumes involved

### Step 2: Propose your own options
Write **3 to 5 distinct approaches** to improving the map. Treat each as a real architectural direction, not a list of small tweaks. For each approach give:
- what changes, naming the files
- effort in days
- dependencies
- pipeline and data-contract changes
- user-facing impact
- risks
- long-term implications (maintenance, lock-in, cost, what it rules out later)

**Do not read the Appendix until you have finished Step 2.**

### Step 3: Evaluate
Now read the Appendix, which summarizes another reviewer's options, and the full write-up at `docs/map-improvement-options.md`. Score every option, yours and theirs together, on this rubric (1–5 per criterion; weights in brackets):

| Criterion | Weight |
| --- | --- |
| Research value (helps find undescribed or native taxa) | 25% |
| Field usability on a phone in Okinawa | 20% |
| Legibility and visual quality | 15% |
| Effort and rewrite risk (higher score = less effort and risk) | 15% |
| Long-term maintainability and lock-in (higher = better) | 15% |
| Location-protection safety | 10% |

Present the scores as a table with weighted totals, then explain where you disagree with the other reviewer and why. Be specific:
- facts in the Appendix that are wrong when checked against the code
- effort estimates that look off
- risks that were missed
- ideas that are better than yours

Do not favour your own options. The point is the best plan.

### Step 4: Synthesize one best-in-class implementation plan
Combine the strongest elements into **one recommended plan**. Where the plan depends on an open decision (see Decisions below), pick a default and justify it. Then describe in 3–5 lines what changes if David picks the other branch.

The plan must contain:
1. **Target architecture.** A short description and a text diagram of the components: renderer, data files, pipeline outputs, versioned data contract.
2. **Phased PRs**, each small enough to review in under an hour. For each PR give:
   - repo
   - scope and files
   - acceptance criteria that can be checked
   - tests to add or update
   - rollback plan

   Phase 0 must contain the quick wins and anything else that is safe to ship before the Okinawa trip.
3. **Data contract changes.** Exact JSON shapes for every new or changed file, a `schema_version` field, a size budget (compressed KB) per file, and how the page behaves when it meets an unknown version.
4. **Location-protection review.** For every new piece of published data, state what precision it reveals and which gate it needs (for example, the sensitive-species list review, or a minimum record count per square).
5. **Performance and offline budget.** First-load weight, time-to-map on a mid-range phone over 4G, and what is cached for weak signal.
6. **Accessibility and i18n checklist.** Cover keyboard, screen readers, contrast in both themes, and EN/JA labels.
7. **Risks register.** For each risk give likelihood, impact and mitigation.
8. **Hand-off notes for the coding agent.** Include:
   - conventions to follow in this codebase
   - what not to touch
   - how to verify each PR (commands, pages to load, screenshots to take)

## Decisions David still has to make (give a recommended default for each)
1. Is the map a desktop research view or a field tool? This chooses between a dependency-free SVG and a real basemap.
2. If a basemap: a hosted free tile service, or self-hosted PMTiles on Cloudflare R2?
3. Species search: publish ~10 km presence per species after the sensitive-list review, or limit search to genus and place names?
4. Add `schema_version` to the data contract now?

## Output
Write everything to `/Users/dklan/projects/personal/ShroomHunt/docs/map-improvement-plan.md`, next to this prompt and the options write-up. Do not commit or push in either repo. Keep it tight and specific: numbers, file paths, line references. No filler.

---

## Appendix: the other reviewer's options (read only after Step 2)

The reviewer read the live site on 2026-11-21. Their findings were:
- The map is about 80 lines of hand-written SVG, inside a ~470-line script.
- `dashboard.json` is 17 KB transferred. Its `mesh` field has 2,763 rows of `[lat, lon, records, species]` on the JIS 2nd mesh.
- `areas/index.json` is 21 KB, with 211 areas and a cell-to-area lookup.
- The projection is equirectangular with longitude scaled by cos(reference latitude), over a Japan box of 122–154°E, 24–46°N.

Weaknesses they observed:
- There is no coastline or other geography. Zoomed into Okinawa, the map is a grid of boxes inside a dashed rectangle.
- The only way to zoom is to pick an area. There is no pan, wheel or pinch.
- Interaction is mouse-only (`mousemove`/`click`), so there are no tooltips on touch screens and no keyboard path on the map.
- The month and kind filters change the area tables but not the map.
- The palette's 1–9 bucket nearly disappears on the dark background, and teal → amber → rose doesn't read as a single low-to-high scale. The legend is `aria-hidden`.
- Every draw rebuilds `svg.innerHTML`, which prevents smooth animated zoom.

**Quick wins (~1 day, no data changes)**
- Pointer events instead of mouse events: tap shows the tooltip, a second tap opens the area.
- One sequential five-step palette, defined as CSS custom properties.
- Island name labels in place of the dashed bbox rectangles.
- An accessible legend, the area dropdown documented as the keyboard route, and an `aria-live` line announcing the selected area.
- A "Locate me" button that highlights the user's ~10 km square. The location stays on the device.

**Option 1: Reference geography and smooth zoom (2–4 days, no dependency)**
- Add a static simplified coastline and prefecture-border file (Natural Earth 1:10m or GSI, run through mapshaper, about 60–150 KB), drawn under the cells with the existing `project()`.
- Add about 40 place labels.
- Add wheel, drag and pinch zoom.
- Keep one `<path>` per colour bucket.
- Upside: stays dependency-free and testable.
- Downside: it tops out at coastlines. No roads, trails or terrain, and the work is thrown away if Option 3 follows.

**Option 2: The map as a research filter (3–5 days, both repos)**
- A metric toggle for what the colour shows: records, species, share of genus-only records, or density of "could be found here" species.
- The month and kind filters recolour the map.
- Species, genus and place search.
- The pipeline emits a lazily loaded `mesh.json`, about +40–100 KB compressed. Per square it holds 12 monthly counts, a genus-only count and counts by kind, plus a species → squares index. The existing `mesh` in `dashboard.json` stays, for first paint.
- Risk: species search publishes ~10 km presence per species, so it should be gated on the sensitive-list review, possibly with a minimum count per square.
- Add `schema_version` to the data contract.
- Stacks on either Option 1 or Option 3.

**Option 3: MapLibre GL with a real basemap (4–6 days)**
- Replace `setupMap()` with self-hosted MapLibre GL JS (~250 KB compressed).
- Basemap: OpenFreeMap, or a Japan PMTiles extract on Cloudflare R2. Optional hillshade.
- Squares become a GeoJSON fill layer built in the browser from `mesh`, so there is no pipeline change.
- The drill-down, `areaAt`, `meshKey`, hash routing and tables stay as they are.
- A dark style plus a light variant.
- Costs: a dependency, about 300 KB of first load, attribution, and a long-term commitment to a tile source. `project`/`unproject` and their tests are deleted. Offline use needs PMTiles plus a service worker.

**The other reviewer's recommendation**
- Ship the quick wins first.
- Then **1 → 2** if the map is a desktop research view, or **3 → 2** if it is a field tool.
- Hold species search until the sensitive-list review is done.
- Add `schema_version` now.
