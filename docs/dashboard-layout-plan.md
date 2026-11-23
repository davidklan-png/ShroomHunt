# ShroomHunt dashboard: layout overhaul plan

23 November 2026 JST · Planner-owned design doc. Workers read it; they don't edit it.

Covers the page at kinokoholic.com/kinokomon/ (and /ja/kinokomon/), built from Bio_HP
`_includes/fungi_dashboard.html`, `assets/js/fungi-dashboard.js` and `assets/css/fungi-dashboard.css`.
Reviewed against Bio_HP `main` at `6a94b4c` and the live site at 1440, 1024 and 390 CSS px.

## 1. David's decisions (2026-11-23)

| # | Decision | Status |
|---|---|---|
| L1 | **Map plus a side inspector panel.** The map stays in view (sticky) on the left. The right-hand panel shows an "All Japan" overview when nothing is selected and the selected area when something is. | Decided |
| L2 | **One search box.** Merge the area dropdown, the Place/Name toggle and the search field into a single combobox. Island groups and islands become the suggestions shown when it's empty. Accept the test rework this causes. | Decided |
| L3 | **The lower charts stay national and are labelled "All Japan"** in Phase 1. Per-area evidence, identification-depth and decade counts come in Phase 2 through a pipeline change. | Decided (approved 2026-11-23) |

Rejected alternatives: (b) widen the map but keep the area below it. It's the cheapest option, but the map still scrolls away while you read an area. (c) A tabbed app (Map / Leads / Stats). It hides the leads, adds page state and gives worse deep links.

## 2. Problems with the current page

1. **The map is too small.** It's 512 px wide in the 1,120 px container at desktop width. `.fd-grid { max-width: 920px; margin-inline: auto }` inside the `.fd` grid shrinks the item to its content width. Five rows of controls also sit above the map, so it starts about 430 px down the page.
2. **Reading an area hides the map.** `#fd-area` renders below the map and runs about 1,350 px for Okinawa Island, so the map you navigated with scrolls out of view.
3. **The lower charts read as if they belong to the selected area.** With an area open, the evidence, decade and unidentified-genera cards still show national figures, with nothing saying so.
4. **The highlighted "1,001 leads" KPI tile points to a section about 1,800 px further down** and doesn't link to it.
5. **Some shipped data is unused:** `dashboard.json` carries `families` and `phyla`, and nothing renders them.
6. **Back doesn't move between areas.** `#area-<id>` deep links work on a full page load, but nothing listens for `hashchange` or `popstate`, and selections use `replaceState`.
7. **Duplicate controls:** the dropdown, the Place/Name toggle and the search input do overlapping jobs.

## 3. Target layout

```
┌ Header (about 180 px): title + JA title · one-line lede · 5 KPI tiles (leads tile links to #fd-leads) ┐
├ Workspace (full container width) ─────────────────────────────────────────────────────────────────────┤
│ Toolbar: [ one search box ] · Japan › Okinawa Island · Locate · Research filters · month chips          │
│ ┌ Map (sticky, about 58%) ──────────────┐ ┌ Inspector (about 42%, min 360 px) ─────────────────────┐ │
│ │ height follows the viewport            │ │ No selection → All Japan overview                      │ │
│ │ legend, zoom, street view as today     │ │ Area → headline, months, sampling note                 │ │
│ │                                        │ │   tabs: Overview | Recorded (n) | Could be found (n)   │ │
│ │                                        │ │   two-line rows: name / family · facts                 │ │
│ └────────────────────────────────────────┘ └────────────────────────────────────────────────────────┘ │
├ Across all of Japan: Leads (2/3) · Where species may hide (1/3) · Evidence · Records by decade ─────────┤
└ Data and method ──────────────────────────────────────────────────────────────────────────────────────┘
```

- **Breakpoint:** two columns at a viewport of 1024 px or more. Below that the blocks stack in this order: header, toolbar, map (not sticky, `clamp(360px, 90vw, 560px)` tall), inspector, research, method.
- **Sticky map:** `top` clears the site nav, and the height is `calc(100dvh - top - gap)`, clamped to roughly 520–820 px. The inspector scrolls with the page.
- **Inspector, no selection ("All Japan"):** suggestion buttons for island groups (fastest route into Okinawa and similar areas), top unidentified genera (top 8), the kind-of-fungus or phylum split from `phyla`, top families from `families`, and a leads teaser linking to `#fd-leads`.
- **Inspector, area selected:**
  - Header: name, group, records · species · squares · unresolved, the sampling note (`samplingNote()` already exists), and the 12-month bar.
  - **Overview** (default tab): evidence mix and top families computed in the browser from the area's species rows, labelled "species-level records". Also the top 5 recorded species and top 5 that could be found here.
  - **Recorded** and **Could be found:** two-line rows with no horizontal scroll at 360 px.
  - The tab choice persists across area changes. Tabs don't go in the URL; that space is reserved for `#species-<key>`.
- **Below the workspace:** headed "Across all of Japan" (JA: 日本全体), so nothing there reads as area-specific.
- **History:** user selections push a history entry; the initial load replaces it; `popstate`/`hashchange` re-select the area or return to Japan.

## 4. Constraints (apply to every phase)

- **Keep the current CSS system.** Don't change `assets/css/site.css`, `styles.css`, `_layouts/`, `_includes/nav.html` or the 1,120 px container. Add no new colour values or tokens. Reuse the existing `.fd-*` components (`fd-card`, `fd-btn`, `fd-seg`, `fd-table`, `fd-bar`, `fd-ms`, `fd-like`, `fd-kpi`, `fd-note`). New classes are allowed only for layout containers (`fd-workspace`, `fd-inspector`, `fd-tabs` and similar), built from existing tokens. Both themes must keep working. The CSS link's `?v=` stamp is bumped.
- **Keep the site rules:** one include serves EN and JA (Liquid `ja` flag plus the JS `T` table) with full parity; 44 px targets; the `[hidden] { display: none !important; }` rule; one polite live region; keyboard operation; a 320 px layout check.
- **Keep the data protection:** no record-level points; species-level masking only; no new data fetched by the browser; Phase 1 makes no change to `assets/data/shroomhunt/**` or the data contract.
- **Keep existing behaviour:** `selectArea()` stays the single selection path; street map, research filters, locate, tap preview and the snapshot/version guards must not regress.

## 5. Phases

| Phase | Repo / task | Scope | Depends on |
|---|---|---|---|
| 1a | Bio_HP `handoff/TASK-007-workspace-layout.md` | Grid, sticky map, inspector with tabs, two-line rows, All Japan overview, reordered and labelled sections, KPI link, history fix | — |
| 1b | Bio_HP `handoff/TASK-008-unified-search.md` | One combobox replaces the dropdown, the Place/Name toggle and the input; empty-state suggestions; test rework | 1a |
| 2 | ShroomHunt `handoff/TASK-021-area-overview-aggregates.md` | Per-area `evidence`, `id_depth` and `decades` over the public record set, plus validator support | TASK-020 merged (`main` `71d2846`); ship in the next data publish |
| 2b | Bio_HP TASK-009 (to write after Phase 2) | The inspector Overview uses the per-area aggregates and the "species-level records" caveat goes | 2 published |
| 3 | Species details | The `#species-<key>` card opens in the inspector, per `species-pages-spec.md` | COL XR keys published |

**Data still unpublished:** TASK-012 (fruiting-only months, `dominant_source`) is built in ShroomHunt but not yet on the live site. Okinawa Island still shows the February and November DNA spikes. Phase 1 displays the sampling note as soon as the data carries it, with no extra work.

## 6. Impact on later work

- **Species pages:** the inspector becomes their home. Two-line rows leave room for the ⚠ and 固有 markers and the expandable details row without adding columns (`species-pages-spec.md` §5).
- **Tests:** layout moves DOM ids. Tests that touch `#fd-area-pick`, the Place/Name toggle or `#fd-area` placement need updating in 1b. Pure helpers added in 1a (evidence mix, top families, tab state, hash handling) get `node:test` coverage.
- **Leaflet:** the street layer must call `invalidateSize()` when the map column resizes (a ResizeObserver on `.fd-map`).
- **Performance:** no new payloads. Rendering 50 rows by default (rather than 300) in the inspector reduces DOM work.
