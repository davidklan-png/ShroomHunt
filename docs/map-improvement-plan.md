# ShroomHunt map implementation plan

21 November 2026 JST

Use a same-origin SVG overview for immediate orientation, with an optional Leaflet street layer for field use. Ship the touch and accessibility fixes first. Before publishing any new occurrence dataset, close the existing location leaks and check every public output. Then add screened genus-only discovery filters without increasing the precision of occurrence locations.

## 1. What ships today

Inspected ShroomHunt `37bc9fc` and Bio_HP `origin/main` at `566a41c`. The live script, CSS and three sampled JSON responses matched Bio_HP byte for byte after decoding. Ran the deployed EN/JA HTML and tracked dashboard assets on a local static server in Chromium at 1,350 and 390 CSS px. Japan and the Okinawa deep link rendered, including the Japanese title. All 25 dashboard unit tests passed. The local test used a deployment snapshot because the checkout's locked Bundler was unavailable. Touch was emulated. The supplied brief displayed its appendix alongside the task. The four options below were written before opening the full review. [Live dashboard](https://kinokoholic.com/kinokomon/) · [Okinawa deep link](https://kinokoholic.com/kinokomon/#area-okinawa-honto)

| Asset | Live gzip body / decoded, bytes | Measured contents |
| --- | ---: | --- |
| `dashboard.json` | 17,149 / 73,124 | 345,509 national records. 2,763 JIS 2nd meshes with 147,580 mapped records. 1,645 cells (60%) are in the faint 1–9 bucket. 67,656 national records are genus-rank. |
| `areas/index.json` | 21,354 / 101,139 | 211 areas (177 tiles, 27 islands, seven groups) and 2,763 cell-to-area entries. The published mesh and index keys match in this snapshot. |
| `areas/okinawa-honto.json` | 17,301 / 98,193 | 11,416 records including 720 assigned by locality name, 29 cells, 441 recorded species rows and 120 candidate rows. The 10,457 unresolved records include ranks above genus. |
| `fungi-dashboard.js` + `.css` | 11,706 / 37,268 | 480 JS lines, 77 lines for `setupMap()` itself. |

All 211 area detail files total 9.52 MB decoded and 1.69 MB under local gzip `-9`, with a 5.1 KB median and 55.5 KB maximum compressed. They load individually on selection. Full live map assets are 50,209 bytes gzip through the picker, or 67,510 with Okinawa detail, excluding the page and shared site assets. [Dashboard payload](https://kinokoholic.com/assets/data/shroomhunt/dashboard.json) · [Area index](https://kinokoholic.com/assets/data/shroomhunt/areas/index.json) · [Okinawa payload](https://kinokoholic.com/assets/data/shroomhunt/areas/okinawa-honto.json)

- **Rendering and hit testing.** Bio_HP `assets/js/fungi-dashboard.js:9–12,59–104,218–294` uses a fitted equirectangular view and five SVG paths grouped by record counts. It has no land geometry. At 390 px the map was 329 px wide. A national cell projects to about 1.3 px before expansion to at least 2.2 px, while hit testing uses the actual mesh. At island edges `areaAt()` gives the raw island rectangle priority over cell ownership, which can disagree with the selected outline. Nine published cells have centers south of the 24° initial view. Panning, pinch and wheel zoom are absent. [Map source](https://github.com/davidklan-png/Bio_HP/blob/566a41c71bd97876ae807f006a5d9cd3541a9220/assets/js/fungi-dashboard.js#L216-L294)
- **Interaction and loading.** `fungi-dashboard.js:267–288,300–357,464–477` has mouse hover, click and a keyboard-operable picker (34 choices plus placeholder). In emulated Chromium a tap could show the tooltip through synthesized mouse events. There is no deliberate touch inspect/open sequence. The picker waits for serial dashboard and index fetches. Zoom waits again for area detail even though the index has the bbox. An index failure is silent after the dashboard loads. Month and kind call `renderArea()` only. `latestOnly()` guards stale success/error, and `replaceState` writes the initial-load deep-link format. [Startup and map code](https://github.com/davidklan-png/Bio_HP/blob/566a41c71bd97876ae807f006a5d9cd3541a9220/assets/js/fungi-dashboard.js#L296-L357)
- **Legibility and tests.** Bio_HP `_includes/fungi_dashboard.html:19–30` hides the legend from assistive tech. `assets/css/fungi-dashboard.css:5–9,33–43,67–75,112` already overrides the dashboard tokens in both themes. The lowest fill compounds `.38` opacity twice, about `.14`, and the tooltip is single-line and positioned against an assumed 300 px width. `tests/fungi_dashboard.test.js` exercises the pure helpers, area fixtures and request sequencing. `unproject` is private and has no direct test. [CSS](https://kinokoholic.com/assets/css/fungi-dashboard.css)
- **Data and protection.** `pipeline/build_dashboard_data.py:25–37,58–76` builds national aggregates and the mesh. `pipeline/build_areas.py:37–41,53–120,134–185,188–286` classifies approximate fungal kind, assigns islands by raw coordinates or name, and exports area-level species, monthly counts and nearby candidates. The builders use different protected-taxon predicates. East Asia candidates ignore the Red List keys, and national named leads exclude CSV names only. Both tolerate a missing Red List when invoked directly. More precise locality, habitat/host prose and elevation survive in the public area files. Overlapping island/mesh membership tied to raw coordinates can disclose a subcell. A cell on an island-box edge can get different owners when record order changes (`build_areas.py:104–112`). `geo_ja.py:4–41` handles place names and distances. The JIS math currently lives in the builders and JS.
- **Delivery.** `pipeline/build_db.py:19–45` loads private working tables including optional DwC-A detail and East Asia 1° aggregates. `publish-dashboard.yml:30–65` rebuilds, replaces area assets and opens a Bio_HP PR for David to merge. `fetch-data.yml:70–79` uploads raw inputs to Releases on **public** ShroomHunt. Confirmed a published release with a 28.7 MB East Asia raw archive. Git-ignoring the files leaves that route public. `docs/architecture-decisions.md:46–61` sets out mesh protection and discovery priorities. `docs/sources.md:23–25` records upstream licensing. [Published release](https://github.com/davidklan-png/ShroomHunt/releases/tag/data-20261121-0204)

## 2. Independent options

Effort is engineering days for one developer, including tests and mobile QA. Add the common publication repair and review separately. Each direction can use the same protected public mesh and existing area routing.

| Option | Changes and dependencies | Pipeline and contract | Field and research impact | Risks and longer-term cost |
| --- | --- | --- | --- | --- |
| A · Bounded SVG reference map · 2–4 days | Keep `setupMap` in Bio_HP `assets/js/fungi-dashboard.js` and update its CSS, include and tests. Draw same-origin, simplified Natural Earth land and bilingual island labels. Keep a stable SVG layer, fit Japan/area, and give it button-based zoom with an explicit pan mode. No browser library. | Generate one compressed geography asset with a pinned source and a repeatable clip/simplify step. Base mesh and routes stay compatible. | Immediate recognition of Okinawa and its neighbors on weak signal. Easy to compare cells and get back to a known view. | Small islands can disappear during simplification. Reference geography offers few usable roads or trails. Keep it as a fallback if detailed mapping follows. |
| B · Progressive raster field map · 4–6 days | Add a pinned Leaflet adapter and vendor JS/CSS to Bio_HP, with the SVG as the initial/failure view. Add a Street map button in the include and reuse picker, summary and history. Request GSI Pale tiles on demand. | Convert approved JIS keys to exact WGS84 cell corner polygons in the browser. The occurrence contract needs no slippy tiles or geometry export. | Easy roads, terrain cues, pinch and pan on a phone. Keep the research workflow available on the same page when map tiles fail. | Raster label language and styling are provider-controlled. Moderate ongoing dependency and network reliance. A future swap to a vector renderer preserves area state and cell geometry. |
| C · Area-first field atlas · 5–7 days | Make the picker and a text list of ranked areas the primary map interface in the Bio_HP include, JS and CSS. Use a small SVG thumbnail of the selected area and plain list results on narrow screens. | Add versioned per-area discovery aggregates via `pipeline/build_areas.py` and the publish workflow. Keep thumbnails purely geographic. | Surfaces a short list of areas to survey and gives the fastest accessible route to their existing tables. Comparison is by area, with limited spatial exploration within Okinawa. | Area size, visit effort and collection bias distort rankings. Low frontend dependency. The atlas can remain a separate alternate view, but would not replace the navigation value of a detailed basemap. |
| D · Primary vector-tile map · 7–11 days | MapLibre GL JS plus PMTiles protocol. Pin vendor assets, two styles and required glyphs/sprites in Bio_HP. Retain an equivalent selector and lightweight map for devices without WebGL. | Build occurrence GeoJSON in browser from the approved mesh. Produce and host a separate versioned public-geography extract with a refresh process. | Most control of labels, roads and contrast. Bounded downloads are possible with a separate offline pack. | GPU/device coverage, Japan tiles, glyphs, caching and long-term ownership add work. ODbL attribution and applicable redistribution terms attach to basemap assets. Costs shift to hosting, refreshes and device QA. |

## 3. Head-to-head evaluation

Scores are 1–5, with 5 best. Totals are weighted means out of 5. All renderer options assume the same baseline publication repair. The reviewer's research option is scored as proposed on the current SVG so it gets credit for its research value without also receiving a basemap's usability score.

| Option | Research 25% | Field 20% | Visual 15% | Effort 15% | Maintenance 15% | Protection 10% | Weighted / 5 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| A · Bounded SVG reference | 3 | 4 | 4 | 4 | 5 | 4 | 3.90 |
| B · Progressive raster field map | 3 | 5 | 4 | 4 | 4 | 4 | **3.95** |
| C · Area-first atlas | 4 | 3 | 3 | 3 | 4 | 4 | 3.50 |
| D · Primary MapLibre + R2 | 3 | 4 | 5 | 2 | 2 | 4 | 3.30 |
| Reviewer's quick wins | 2 | 3 | 3 | 5 | 5 | 4 | 3.45 |
| Reviewer 1 · Geography + continuous zoom | 3 | 4 | 4 | 4 | 4 | 4 | 3.75 |
| Reviewer 2 · Research filter + species search | 5 | 3 | 4 | 2 | 3 | 2 | 3.40 |
| Reviewer 3 · MapLibre basemap, hosted first | 3 | 4 | 5 | 2 | 3 | 4 | 3.45 |

- **Findings to correct.** The size and projection estimates hold. Touch taps can trigger synthetic hover and click, and the dropdown already supports keyboard use. The shortcoming is a reliable touch inspection flow and equivalent access to cell information. `unproject` is private. Rendering shares `state.box`, index and area selection state with the drill-down. The 300 KB MapLibre estimate omits potentially substantial style, sprite, glyph and tile transfers, and depends on the build and version. Treat it as a starting guess. [Full review, lines 19–38 and 95–111](map-improvement-options.md)
- **Effort and missed risks.** A day covers the core palette, legend and tap flow. Budget another ½–1 day for on-device geolocation, permission states and mobile checks. Reviewer's Option 1 fits 2–4 days for coastline, labels and discrete fits. Continuous touch/pointer zoom and scroll arbitration are closer to 4–6 days with QA. Five SVG nodes reduce DOM churn, while rebuilding path coordinates still scales with cell count. The coastline retains value as a weak-signal fallback. [Full review, lines 54–79](map-improvement-options.md)
- **Research proposal.** The reviewer's month/kind recolouring and genus-only metric are more useful than the area atlas for deciding which squares to investigate. A monthly total and a kind total cannot answer a joint month × kind query, and one genus-only total cannot yield its filtered share. Species richness needs distinct eligible taxa per slice. Candidate density would turn a relative area-level ranking into an unsupported cell-level inference. Budget 5–8 days for the narrowed research release after the privacy repair. The proposed 40–100 KB is unverified with a species reverse index. Small counts, overlapping views, and joins to old snapshots matter even after reviewing the sensitive list. [Full review, lines 81–93](map-improvement-options.md)
- **Basemap proposal.** Budget 5–8 days for a hosted MapLibre release with fallback, touch, themes and bilingual QA, or 7–11 with self-hosting and an extract refresh. PMTiles uses HTTP ranges and does not itself make a map available offline. Conversely, an open tab or a small approved local package is possible without PMTiles. The reviewer is right to retain browser-built cell polygons, stable routes and a version guard. In the recommended hybrid, projection helpers still serve the fallback. [Full review, lines 95–111](map-improvement-options.md) · [PMTiles storage](https://docs.protomaps.com/pmtiles/cloud-storage) · [OpenFreeMap service and terms](https://openfreemap.org/)

## 4. Recommended architecture and decisions

One shared camera/selection model feeds the existing lightweight overview and the optional detailed renderer. The site keeps the same area IDs, tables, EN/JA routes and `#area-<id>` entry point. A map tap previews the square and offers a visible **Open area** action. A second tap on the same square can open it. The picker, search results and map use the same area selection function. The preview shows the full JIS rectangle, with lower zoom generalized visually only by an explicitly labelled overview treatment. Geolocation highlights one JIS cell locally and does not follow the user through the basemap.

```text
Fetch data → private input releases → DuckDB → shared public eligibility + JIS geometry
                                              ├── dashboard.json, areas/*.json (v1, snapshot)
                                              └── mesh-research.json (v1, same snapshot, lazy)
Natural Earth + curated public names → japan-outline.json (independent v1)
              Validated static assets → pipeline PR → Bio_HP /assets/data/shroomhunt/
                              → existing dashboard state, picker, hash, area tables
                                ├── SVG overview + coastline (first paint/fallback)
                                └── Leaflet + GSI Pale tiles (explicit Street map action)
```

1. **Field tool with a lightweight first view.** In Okinawa, roads and familiar places make the mesh usable for orienting on a phone. The SVG still opens Japan or the selected island before tile traffic begins.
   - Desktop branch keeps the SVG as the sole renderer and omits Phase 2.
   - Add discrete +/−/reset and pan controls if desktop inspection needs them.
   - The choices of mesh metrics, versioning and area routing carry over.
2. **Hosted GSI Pale tiles on demand.** GSI documents real-time embedding with attribution. Use the Japan Pale endpoint, include all layer-specific credits from its listing, and verify the exact Okinawa zoom bands before merge. Tile-embedded labels follow the source. The app supplies bilingual island/place labels and controls. [GSI tile use and attribution](https://maps.gsi.go.jp/development/ichiran.html) · [GSI website terms](https://web2.gsi.go.jp/ENGLISH/page_e30286.html) · [Leaflet reference](https://leafletjs.com/reference-1.9.4.html)
   - R2 branch uses licensed OSM-derived vector PMTiles with a pinned MapLibre adapter, local styles/glyphs and documented extract refresh.
   - Use a production custom domain, CORS and `206` range probes. Keep the public-geography dataset and attribution separate from occurrences.
   - Budget storage and Class B reads. Add an explicit offline package separately if needed. Replace the Phase 2 adapter only. [OSM licence](https://www.openstreetmap.org/copyright) · [PMTiles with MapLibre](https://docs.protomaps.com/pmtiles/maplibre) · [R2 public buckets](https://developers.cloudflare.com/r2/buckets/public-buckets/) · [R2 CORS](https://developers.cloudflare.com/r2/buckets/cors/) · [R2 pricing](https://developers.cloudflare.com/r2/pricing/)
3. **Genus and place search.** Curated geographic terms jump to an area. Reviewed genus-only names return coarse area links. This directs verification without attaching a binomial to a cell.
   - The species branch adds a separate lazy versioned index only after David and a taxon reviewer approve that disclosure.
   - Require an affirmative species allowlist, support from multiple independent records per species and mesh, and joint review with prior releases.
   - A separate PR specifies the exact index, tests suppressed entries and makes the index removable without disabling search by place or genus.
   - **Amended 2026-11-25 (David, `filters-and-search-plan.md` S3):** the species index ships as `species/squares.json` (TASK-028, PR #16) for every public species under the masking gate and the map's source-precision rule, single-record pairs included. The allowlist, the several-records rule and the taxon-reviewer sign-off above no longer apply to it. The removability rule stays: the page works without the file.
4. **Add `schema_version` now.** Land readers first and publish all occurrence files in one versioned snapshot. Keep existing URLs and the base mesh tuple.
   - If deferred, land Phase 0 UI and the static field renderer using the existing tuple and shape checks.
   - The shared mask, whole-cell assignment and legacy redaction still ship as compatible fixes.
   - Defer the research extension until its producer and consumer can be versioned together.

## 5. Phased pull requests

Each row is one source PR, except the explicitly generated Bio_HP data PRs. Keep generated blobs out of source diffs. Attach a summary of file count, snapshot, size checks, release-gate results and Okinawa before/after counts to each generated PR. Aim for under an hour of review per PR. Field work can start with 0C–0E using fixtures while David reviews protection inputs. Public occurrence changes depend on 0A–0D and 1A–1C. Prioritize Phase 0 before the trip.

| PR / repo | Scope and files | Acceptance and tests | Rollback |
| --- | --- | --- | --- |
| **0A · ShroomHunt** · private input staging | `.github/workflows/fetch-data.yml`, `publish-dashboard.yml`, `pipeline/fetch_latest_release_assets.py`. Stop new raw releases on the public repo. Use a private repository for staging releases, with a narrowly scoped token and a visibility check before upload. Pause the public publish path until 1C passes. | Missing/misconfigured private destination fails closed. Fixture tests for complete input selection, absent inputs and a public destination. Inspect release assets and logs for raw content exposure. David inventories existing public releases and authorizes removal separately. | Leave raw publication disabled while reverting or restoring staging. |
| **0B · ShroomHunt** · shared taxon gate | Add `pipeline/public_taxa.py`, update `build_dashboard_data.py`, `build_areas.py`, `analysis/01_neighbour_candidates.sql` and the reviewed inputs in `data/reference/`. Apply keys, accepted names and aliases to JP/East Asia/neighbour rows and candidate/leads queries. Review ambiguous match types from `fetch_redlist.py`. | Require a reviewed mask and coverage manifest. Test synonyms, null keys, infraspecific ranks, ambiguous genus-only rows, and East Asia/national lead paths. An absent mask blocks publishing. Unresolved names block publication or quarantine the affected group. Log aggregate audit counts only. | Block new exports if the shared gate is rolled back. |
| **0C · Bio_HP** · touch and visual fixes | `assets/js/fungi-dashboard.js`, `.css`, `_includes/fungi_dashboard.html`, `tests/fungi_dashboard.test.js`. Add inspect/open, real legend, labelled picker, live selection summary, wrapping details and theme-local sequential colours. Use public place labels as they become available. | Real iOS/Android tap then open, mouse hover/click parity, keyboard picker/open/reset, pointer cancellation during scroll, 320 px text flow and dark/light contrast. Unit-test the tap transitions and bucket boundaries. | Revert the UI diff alone. |
| **0D · Bio_HP** · early routing, contract readers and locate | Same files. Fetch dashboard/index concurrently. Fit from a valid index on initial hash or selection. Validate versions, snapshot, IDs and shapes. Move area load/error status to the visible map/picker. Tie requests to the latest selection. Add **Locate me** with a fine-position-to-cell conversion in memory. Use a separate geography-only/error state when safe data are unavailable. | Unit-test v0 migration, v1/unknown/mismatched files, stale and failed fetches, invalid hash, mesh boundary and pointer round trip. Browser tests cover delayed detail, two quick selections, unsupported geolocation, denied/timeout/inaccurate fix, and zero location-related network or storage writes. | Revert locate and camera changes independently. Keep fail-closed readers after 1C. |
| **0E · ShroomHunt → generated Bio_HP PR** · geography | Add `pipeline/build_map_reference.py` and `data/reference/map_places.csv`. Clip Natural Earth 1:10m land v5.1.1, then simplify with topology preservation. Render below cells with curated bilingual anchors. Replace dashed island rectangles with labels and retain a distinct selection outline. | Reproducible bytes, ≤36 KiB gzip, Okinawa Hontō outline and island labels, no occurrence inputs. Fallback on bad version/404. Check 20–46° initial extent, Naha and Yanbaru at 390 px. This asset can ship via a geography-only pipeline PR while occurrence publication is paused. | Restore the previous reference asset or omit it. |
| **1A · ShroomHunt** · deterministic mesh ownership | `pipeline/geo_ja.py`, both builders and pipeline tests. Quantize raw coordinates to integer JIS keys once with a common envelope. Assign area by the cell center and deterministic island priority, then first mesh. Keep known routes for areas whose approved records drop to zero. | Boundary fixture shared with JS, row-shuffle invariance, identical dashboard/index key sets, stable IDs, Okinawa preview. Verify output invariance when eligible input points move within their JIS 2nd mesh. | Keep publishing paused until the last approved geometry is restored. |
| **1B · ShroomHunt** · sanitize area detail | `pipeline/build_areas.py`, `build_db.py` only if needed for eligibility fields, and pipeline tests. Remove occurrence-derived localities, habitat/host strings and elevation from public fields. Exclude name-only assignments from spatial outputs. Base candidate math on snapped source cells and emit 50 km bands. Preserve area tables and empty states. | Test singleton locality/host/elevation fixtures, coordinate flags and coarse/obscured sources, candidate and group output. Verify all table associations come from complete mesh-cell unions. Redaction and eligibility account for the changed Okinawa totals. | Withdraw affected detail assets through the publisher or restore a confirmed safe snapshot. |
| **1C · ShroomHunt → generated Bio_HP PR** · enforce public contract | `pipeline/build_dashboard_data.py`, `build_areas.py`, add `pipeline/validate_dashboard_publish.py`, tests, and `publish-dashboard.yml`. Stamp v1 and one bundled snapshot, validate in a staging directory before the Bio_HP clone/copy, and re-enable the normal PR flow. Cut over Bio_HP readers to v1 occurrence files and purge legacy edge caches at release. | Schema/size validation, mask across **every** published list, referential integrity, stable routes, exact rectangle checks, asset allowlist, and clean staging diff. Deny an intentionally unsafe fixture before any push. Review workflow result and live headers. | Publish the last approved complete snapshot or withdraw affected occurrence files and serve geography-only. Purge changed URLs at the edge. |
| **2 · Bio_HP** · hosted street overlay | Add `assets/js/fungi-field-map.js` and pinned Leaflet distribution/notice under `assets/vendor/leaflet/`. Update dashboard JS/CSS/include/tests. Use canvas fill for mesh rectangles, normal Leaflet pan/pinch with explicit entry, real-world label anchors, tile status and SVG fallback. Inside the detailed view, offer a clear escape and leave page wheel/scroll free outside the view. | First paint makes zero GSI requests. Pinch/drag, click/inspect and selection agree between renderers. Confirm attribution, 44 px controls, tile-blocked fallback and 4G budget. Test exact corners, camera/selection parity and tier switching. | Remove the Street map entry and lazy adapter, retaining SVG and all data. |
| **3A · ShroomHunt → generated Bio_HP PR** · research payload | Add `pipeline/build_mesh_research.py`, using shared sanitization and the `kind()` classifier. Wire `mesh-research.json` into the publish validation/staging set and gated data PR. | Joint month/kind fixture, distinct evidence-event fixtures, genus-only rank, missing month, support thresholds, small slices, audit for differencing and budget. Approval is required before creating a public PR containing the payload. | Remove the optional payload in the next pipeline PR and purge it at the edge. |
| **3B · Bio_HP** · research controls | Existing dashboard files/tests. Add **Research filters** toggle and bilingual place/genus search. Reuse month/kind state with synchronized controls in research mode. Keep all-time records the default view. Show band labels and an accessible list of eligible visible cells/areas. | Invalid/missing research leaves overview controls, place search and tables usable. Test missing vs suppressed slices, selection retention, picker navigation, threshold legend, screen-reader output and weak-signal latency. | Disable the research entry point and remove the optional data via 3A rollback if disclosure is involved. |

If the taxon or publication review cannot finish before travel, ship the approved geography and UI and use the geography-only state wherever approved occurrence data are unavailable. Fixing the UI does not resolve an unsafe payload or erase versions already distributed.

## 6. Public data contract

Paths below are relative to Bio_HP `assets/data/shroomhunt/`. `UInt` is a nonnegative integer, `Day` is `YYYY-MM-DD`, `Id` is a stable area slug, `MeshKey` is `row:col` in decimal, `Bins12` is 12 nonnegative integers from Jan through Dec, `Box` is `[south, north, west, east]` in WGS84 degrees, and `Pair` is `[name_en, name_ja]`. `snapshot` is the 64-character lowercase SHA-256 digest of the canonical approved occurrence bundle before stamping. Include research in the digest when present, so changing optional data still invalidates a stale join. Reference geography versions independently. Use deterministic ordering and stable tie breaks.

| File | Maximum gzip KiB per file | Treatment of unknown version or invalid content |
| --- | ---: | --- |
| `dashboard.json` | 22 | Show geography and a localized data status. Render the picker and matched detail if their index validates. |
| `areas/index.json` | 25 | Show valid national counts and reference geography. Disable area selection and announce why. |
| `areas/<id>.json` | 64 each, 24 for `okinawa-honto` | Keep the index-based zoom and picker. Withhold the selected detail and offer retry. |
| `japan-outline.json` | 36 | Leave SVG cells and the picker usable. Hide only the reference geography. |
| `mesh-research.json` | 60 | Leave the initial metric, place search and tables usable. Show unavailable research controls/status. |

Freeze v1 shapes as follows. Omitting `schema_version` is v0 only during reader-first staging. Once cut over, public readers accept v1 only for occurrence files. An index/detail mismatch or dashboard/index mismatch gets one fresh refetch with cache bypass. If it persists, independently valid layers can render and all joins across snapshots are disabled.

```text
Kind = "m" | "l" | "o"
AreaKind = "island" | "tile" | "group"
MeshRow = [center_lat: number, center_lon: number, records: UInt, species: UInt]
Dashboard {
  schema_version: 1, snapshot: string, generated: Day,
  totals: {records: UInt, species: UInt, genera: UInt, families: UInt, datasets: UInt,
           georeferenced: UInt, to_species: UInt, sensitive_species: UInt,
           sensitive_records: UInt, redlist_names: UInt},
  evidence: [basis: string|null, records: UInt][],
  id_depth: [rank: "species"|"genus"|"higher", records: UInt][],
  phyla: [phylum: string, records: UInt, species: UInt][],
  decades: [decade: UInt, records: UInt][],
  families: [family: string, species: UInt, records: UInt][],
  mesh: MeshRow[],
  leads: [species: string, family: string|null, neighbour_records: UInt, mo_records: UInt, triage_hint: string][],
  lead_totals: {candidates: UInt, with_mo: UInt, with_unresolved_jp_genus: UInt},
  unresolved_genera: [genus: string, family: string|null, records: UInt][],
  mushroom_observer_japan: UInt,
  sources: {key: string, doi: string, records: UInt|null, created: string|null}[]
}
AreaBase {id: Id, kind: AreaKind, name_en: string, name_ja: string,
          group_en: string, group_ja: string, bbox: Box}
AreaIndexRow = AreaBase + {records: UInt, species: UInt, members?: Id[]}
AreaIndex {
  schema_version: 1, snapshot: string, areas: AreaIndexRow[], cells: {[MeshKey]: Id},
  masked_records: UInt, has_habitat: false, has_neighbours: boolean
}
AreaFile = AreaBase + {
  schema_version: 1, snapshot: string, members?: Id[], member_names?: {[Id]: Pair},
  totals: {records: UInt, by_name: 0, species: UInt, unresolved: UInt, squares: UInt},
  months: Bins12, top_localities: [], species: RecordedRow[], possible: CandidateRow[]
}
RecordedRow {
  n: string, f: string, k: Kind, r: UInt, m: Bins12, sq: UInt, y: [first: UInt,last: UInt]|null,
  e: {s?: UInt,d?: UInt,o?: UInt}, h: [], l: [], nm: 0, el: null, i?: Id[]
}
CandidateRow {
  n: string, f: string, k: Kind, s: UInt, d: UInt, w: string, wj: string, m: Bins12, i?: Id
}
MapReference {
  schema_version: 1, source: {name: "Natural Earth", release: string, url: string}, bbox: Box,
  land: Polygon[], places: {en: string, ja: string, aliases: string[],
                             at: [lon: number,lat: number], area_id?: Id}[]
}
Polygon = Ring[]
Ring = [lon: number,lat: number][]
Research {
  schema_version: 1, snapshot: string,
  slices: [month: UInt, kind: "all"|Kind,
           cells: [key: MeshKey, count_band: UInt, genus_share_band: UInt|null][]][],
  genera: [reviewed_genus: string, areas: Id[]][]
}
```

- `members`, `member_names` and row-level `i` are present only on group outputs, as applicable to each shape. `cells` references island/tile IDs only. Bounds and group member incidence derive from complete assigned JIS 2nd meshes. For zero-record entries, use a public reference bbox and empty data. `has_habitat` is false until a coarse, governed habitat field exists. Keep the existing area columns and use an em dash for withdrawn details.
- Compute `row = floor(lat × 12 + 1e-9)` and `col = floor(lon × 8 + 1e-9)` in both hosts. Accept finite points and resulting cells in the shared half-open envelope `[20,46) × [122,154)`. A rectangle spans `[row/12,(row+1)/12] × [col/8,(col+1)/8]`. Round the public center to four decimals as before, reconstruct exact edges from its key and use longitude-first polygon coordinates. Keep full cells across shorelines and island bounds.
- Candidate `s` is the existing 1–100 relative priority within kind. `d` is the lower edge of a 50 km band, `w`/`wj` identify a broad source region, and `m` comes from eligible occurrences in those nearby source populations. Label this a nearby-evidence lead. Use one deterministic full-cell centroid for each source mesh and a public fixed area center, with East Asia kept at 1°.
- `land` is WGS84 multipolygon coordinates, each polygon with a closed outer ring followed by any closed holes. Curated anchors and aliases are geographic references. Select a label via `area_id` only if it exists in the validated area index. Keep Okinawa geometries at a tested simplification tolerance. [Natural Earth land](https://www.naturalearthdata.com/downloads/10m-physical-vectors/10m-land/) · [Natural Earth terms](https://www.naturalearthdata.com/about/terms-of-use/)
- Research month `0` means all months including unknown. A selected month uses only that month. Each allowed month and kind pair is aggregated from eligible records together, with one row per cell. `count_band` indexes `10–24, 25–49, 50–99, 100–249, 250+`. `genus_share_band` indexes `0–<20%, 20–<40%, 40–<60%, 60–<80%, 80–100%`. It is `null` when share support is withheld. A missing cell means no public estimate. A missing slice means the combination was withheld. Base record totals and unfiltered distinct species counts stay in `dashboard.json`. Count and genus share ship first. A distinct-species metric per slice needs separate analysis and review.

## 7. Location-protection gates

David and a taxon reviewer sign off on the eight-entry commercial list, relevant Red List coverage and match exceptions before publishing the new occurrence snapshot. Resolve accepted and verbatim names, known synonyms and infraspecific IDs across inputs. Quarantine uncertain matches and genus-only records in a protected genus when the underlying identification cannot rule out a protected taxon. Use the same gate for the map, all named lists and research aggregates. Keep only approved nationwide aggregate totals for masked records. Use tagged fixture taxa in tests. Inspect all assembled JSON **before** the public branch or PR is created. [Japan fungal Red List source](https://www.gbif.org/dataset/9bac0c5b-7161-409b-ab96-c3bf3f7d7ac1)

| Public surface | Precision revealed | Release gate |
| --- | --- | --- |
| National dashboard and lead lists | National totals and approved neighbouring leads. Base occurrence counts and richness at one JIS 2nd mesh. | Shared key/name/alias gate and matched mesh/index keys. Validate coordinate range, source flags, uncertainty and already-obscured records. A source with coarser precision stays coarse or out of the spatial subset. Enforce a field allowlist and keep upstream DOI attribution. |
| Index and area tables | Full-cell unions, area names, named eligible taxa and aggregate timing. Candidate distances in 50 km bands derived from public-resolution geography. | Whole-cell invariance test, raw field deny fixtures, empty metadata for withheld areas and release diff review. No name-only anchoring, specimen IDs, observer names or occurrence-derived local text/elevation. |
| Coastline, labels, tiles | Public reference geography, including streets at finer scales. No occurrence relationship is encoded in their geometry. | Pin geography and provenance. Display credits below or on each renderer. Keep occurrence overlays as complete JIS rectangles. Attach CC BY-NC 4.0 and existing source DOI citations to derived fungal data. [CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/) |
| Research cell bands | Approximated counts and genus-only share at one JIS 2nd mesh for an approved month/kind slice. | Deduplicate stable source IDs. Start with ≥10 eligible records across ≥3 independent documented sampling events per public slice, and at least five observations in each side of the genus-share calculation. Unknown support withholds the slice. Test complementary suppression against all published totals, sibling slices, area tables and previous downloadable snapshots. Suppress additional bins or an entire slice when a small group can be isolated. |
| Reviewed genus search | Genus-only evidence at an approved area composed of complete cells. No month or finer breakdown for search. | Reviewed genus allowlist plus ≥10 eligible genus-rank records across ≥3 meshes and ≥3 independent sampling events in the area. Use leaf areas only. Taxon review must cover rare/monotypic genera and relationships to species rows and prior outputs. |
| Locate me | One device-side JIS cell. | User-initiated one-shot from a secure origin. Discard raw position after calculating the cell. Denial/timeout and a fix spanning multiple cells get a localized status. No position in requests, tile-camera changes, analytics, URLs, or persistent storage. Clicking **Open area** remains a separate user action. [Geolocation behavior](https://developer.mozilla.org/en-US/docs/Web/API/Geolocation/getCurrentPosition) |

Audit existing public release assets, Bio_HP history and edge caches separately. Document the window of exposure and David's decision about withdrawal. After a corrected snapshot is approved, clear superseded edge copies where possible. Previously downloaded or forked copies can persist. A Git revert is a rollback for UI, while data remediation requires an approved replacement or withdrawal.

## 8. Performance and offline budget

- **Default path.** Budget ≤101 KiB gzip for dashboard JS (13), dashboard CSS (5), dashboard data (22), index (25) and reference geography (36), plus ≤24 KiB for a direct Okinawa link. A scratch Natural Earth clip/simplification at 0.01° was 30,868 gzip bytes before labels and retained land at Naha, Yanbaru, Ishigaki and Iriomote. No Leaflet, GSI, research payload, or area prefetch for a plain landing. Target ≤200 KiB transferred across the whole page through a selectable map. Fetch dashboard/index concurrently and paint as data validates. The map renders ahead of web fonts.
- **Timing targets.** On a mid-range phone with a cold cache at 2 Mbps down, 0.75 Mbps up and 150 ms RTT, target p75 first meaningful SVG ≤2.5 s from navigation, picker/geography ready ≤3.0 s, direct Okinawa table ≤3.5 s. Capture at least five cold runs with cache disabled and record median/p75 and device. Target a warm tap-to-preview below 100 ms. These are acceptance targets for implementation.
- **Street mode.** Pinned Leaflet 1.9.4 JS + CSS measured 45,956 gzip bytes. A representative 3×3 Okinawa GSI Pale sample was 96,545 bytes at z9, 178,964 at z10 and 235,631 at z11. Fit at max initial z9, no retina multiplier or speculative adjacent tile load and ≤160 KiB incremental for the first Okinawa street view. Higher zooms load tiles only through interaction. Keep SVG usable while the adapter loads. On initial tile timeout or provider failure, return to SVG with a retry action. Verify the budget on the actual 390 px viewport. [Source tiles and zoom coverage](https://maps.gsi.go.jp/development/ichiran.html)
- **Weak-signal/offline behavior.** Retain the loaded approved map, reference, picker, opened areas and optional research in tab memory. Bound detail cache to five recently used files from the same snapshot. On the same open tab, cached Okinawa detail and the SVG should work in airplane mode. An unopened area reports unavailable and keeps selection/retry controls. Let normal HTTP caching handle static assets. Keep occurrence files revalidatable and leave third-party tiles to provider/browser caching. Cold offline reopen is outside this release. Any later offline pack needs explicit download, expiry and content recall behavior.

## 9. Accessibility and i18n

- Keep the map a labelled figure with a real legend. Offer a persistent focusable text summary, a native area picker and a direct **Open area** button, plus an accessible cell/area results list for research. Avoid a tab stop on each of 2,763 cells. Name +/−/reset, use Enter/Space for buttons and Escape to exit detail mode or dismiss the preview. Preserve focus on renderer toggle and return it to the initiating control when closing.
- Use one concise polite live region for settled selection/load changes. Avoid broadcasting pointer movement or the entire table. Keyboard users must be able to open Okinawa, read equivalent counts and reach tables. Test VoiceOver on iOS and a desktop screen reader. Allow vertical scrolling across the map before entering detail mode and never disable page pinch zoom on the whole page.
- Give touch controls at least 44×44 CSS px. Keep visible focus, 4.5:1 normal text and 3:1 for meaningful boundaries and controls. Test the lowest visible bucket on water, land and street tiles. Keep a two-tone selected outline, state labels and numeric bands so colour alone carries no meaning. Respect reduced-motion settings and 200% text zoom.
- Pair EN/JA strings in dashboard `T` and Liquid using the document lang. Include touch hints, metrics, filter and status copy, search labels and attribution. Reuse `monthName`, support Kanji/Kana and case/diacritic-insensitive place aliases, and leave Latin taxon names intact. Test translated focus labels and long Japanese names at 320 px. Keep provider label language distinct from the bilingual application labels.

## 10. Risks

| Risk | Likelihood | Impact | Mitigation |
| --- | --- | --- | --- |
| Protected synonym or old extract exposes a taxon | High | High | Pause new exports, use reviewed identity resolution across sources, scan complete snapshots and audit legacy copies. |
| Overlapping views or repeated data releases isolate a small group | Medium | High | Whole-cell geographic boundaries, fixed bands, documentation of independent support, complementary suppression and side-by-side output review. |
| GSI outage, throttling or attribution change | Medium | Medium | Explicit activation, no bulk requests, verified credits and an independently usable SVG. |
| Delayed fetch changes camera/hash after a second selection | Medium | Medium | Use the current request token for both camera and detail. Validate area IDs, version and bundle before committing state. |
| Coast simplification loses islands or tap selects a neighbor | Medium | Medium | Preserve relevant land polygons, use exact cell edges at local zoom and test both renderers at shared boundaries. |
| Collection effort and duplicated surveys dominate genus counts | High | Medium | Show both record and genus-share options with their denominators/limits, require independent sampling support and label results as evidence for follow-up. |
| Phone starts cold offline or the browser discards the tab | Medium | Medium | Make offline behavior explicit, validate the warm route and keep base assets and Okinawa detail under their budgets. |

## 11. Coding agent handoff

- In Bio_HP follow `AGENTS.md` test-first workflow, root-level assets, one shared `_includes/fungi_dashboard.html` and `node:test` CommonJS exports. Keep the backend, global theme CSS, overall Jekyll layouts, unrelated dashboard charts and page structure outside the diff. Keep the current area-table interactions and route IDs. Limit table changes to privacy-safe values, their labels and load/error behavior. In ShroomHunt keep DuckDB 2.0.1, DOI provenance and the pipeline-to-site PR handoff. Do not edit generated JSON by hand.
- For 0C, 0D, 2 and 3B run `node --test tests/fungi_dashboard.test.js`, `./scripts/run-site-js-tests.sh` and `python3 scripts/tdd_guard.py --against origin/main` in Bio_HP. Use the Bundler version in `Gemfile.lock`, then `bundle exec jekyll serve --host 127.0.0.1`. Visit `/kinokomon/`, `/kinokomon/#area-okinawa-honto`, `/ja/kinokomon/#area-okinawa-honto`, `#area-g-okinawa-prefecture`, and a `kind: "tile"` deep link from the fixture index. Exercise quick selection/clear, valid and invalid payloads, tile blocking and both input modes.
- For 0A, 0B, 0E, 1A–1C and 3A run `python3 -m unittest discover -s tests -p 'test_*.py'` in ShroomHunt after adding the pipeline tests. Install `duckdb==1.1.3` under Python 3.12. Run the three commands in `publish-dashboard.yml:34–44` against private inputs, with `--db` pointed at a scratch DB and `--out` paths in a staging directory. Run the new reference/research builders when relevant and `python3 pipeline/validate_dashboard_publish.py <staging-directory>` before staging to Bio_HP. Check representative files with `gzip -9c <file> | wc -c` and use `curl --compressed -w '%{size_download}\n'` on live equivalents after merge. Exercise the workflow with a synthetic unsafe file and missing required inputs before allowing it to open a real data PR.
- Capture 1,350 px Japan overview, 390 px Okinawa in dark and light, 320 px Japanese with legend and picker, keyboard-visible focus, empty/error detail, street-layer attribution, and airplane-mode fallback with Okinawa already open. For Phase 3 capture a populated filter slice, a withheld slice and the genus/place result list. Save transfer/timing measurements beside the PR screenshots and confirm the real Okinawa screen on Safari iOS and Chrome Android.
