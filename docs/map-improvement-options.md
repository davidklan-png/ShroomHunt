# ShroomHunt map: improvement options

2026-11-21 · David Klan · Claude doc: https://claude.ai/artifact/CkPqrk5QN9KkriAxrJnALa (private)

## Summary

The map on https://kinokoholic.com/kinokomon/ can improve a lot without rewriting the dashboard. The map is about 80 lines of a roughly 470-line script, and nothing else depends on how it draws.

The real decision is **Option 1 vs Option 3**:
- **Option 1** keeps the map a lightweight, dependency-free illustration.
- **Option 3** makes it a field tool on a real basemap.
- **Option 2** (the map as a research filter) layers on top of either one.

**Recommendation:**
1. Ship the quick wins first.
2. Then do **1 → 2** if the dashboard is mainly a desktop research view, or **3 → 2** if it will be used on a phone in the field.
3. Hold Option 2's species search until the sensitive-species list is reviewed.

## Current state

The map is hand-written SVG with no map library. This is based on reading the live site on 2026-11-21.

| Piece | Detail |
| --- | --- |
| Code | [`assets/js/fungi-dashboard.js`](https://kinokoholic.com/assets/js/fungi-dashboard.js) in Bio_HP (26 KB, plain JS in a single function). The map section is `setupMap()` plus the helpers `project`, `unproject`, `zoomBox`, `bucket`, `meshKey` and `areaAt`, exported for `tests/fungi_dashboard.test.js` |
| Styles | [`assets/css/fungi-dashboard.css`](https://kinokoholic.com/assets/css/fungi-dashboard.css): `.fd-map__b0`–`b4` (colour buckets), `.fd-map__isle` (dashed island boxes), `.fd-map__sel`, `.fd-map__tip` |
| Projection | Equirectangular, longitude scaled by cos(reference latitude). Japan box 122–154°E, 24–46°N |
| Data | [`dashboard.json`](https://kinokoholic.com/assets/data/shroomhunt/dashboard.json) (17 KB transferred): `mesh` holds 2,763 rows of [lat, lon, records, species] on the JIS 2nd mesh (~10 km). [`areas/index.json`](https://kinokoholic.com/assets/data/shroomhunt/areas/index.json) (21 KB): 211 areas with bbox, records and species, plus a cell → area lookup. One `areas/<id>.json` per drill-down |
| Interaction | Mouse hover shows a tooltip. Clicking the map or using the dropdown selects an area, zooms to its padded bbox and sets `#area-<id>`. The month and kind buttons filter only the area tables |
| Pipeline | The ShroomHunt "Publish dashboard" workflow rebuilds the JSON and opens a PR on Bio_HP |

**Weaknesses seen on the live page**
- **No geography.** Zoomed into Okinawa Island, the map is a grid of boxes inside a dashed rectangle, with no coast, towns or Yanbaru.
- **Zoom only by choosing an area.** There is no pan, wheel or pinch zoom.
- **Mouse only.** Hover and click use `mousemove` and `click` on the SVG, so tooltips never appear on touch screens, and there is no keyboard path on the map.
- **The map ignores the filters.** Month and kind change the tables but not the map, which always shows all-time record counts.
- **Palette.** The 1–9 bucket nearly disappears on the dark background, and teal → amber → rose doesn't read as one low-to-high scale. The legend is `aria-hidden`.
- **Full redraw.** Each draw rebuilds `svg.innerHTML`. That's fine for 2,763 cells, but it blocks smooth animated zoom.

## Constraints any option must respect

1. **Location protection.**
   - No record-level points, and nothing finer than the ~10 km square.
   - Red List and commercially collected species stay off the map and out of every list.
2. **No massive rewrite.**
   - Keep the drill-down, area tables, hash routing and EN/JA labels working as they are.
   - Change the map section and its data, not the page.
3. **Static hosting.** Jekyll on Cloudflare, with no server code. Data arrives as PRs from the ShroomHunt pipeline.
4. **Licence.** The data is CC BY-NC 4.0, so non-commercial use only. Any basemap or tile service must allow that, and its attribution must show.
5. **Theme.** The site is dark-first, and light mode is only half applied (`styles.css` forces dark tokens). Map colours must work in both themes.
6. **Tests.** Pure helpers stay exported and tested.
7. **Mobile.** The map will be used in Okinawa on a phone, possibly with weak signal.

## Quick wins (do regardless, about a day)

- **Pointer events.** Swap `mousemove`/`click` for `pointermove`/`pointerup`, so a tap shows the tooltip and a second tap opens the area.
- **One sequential palette.** Five steps of a single ramp, with the lowest bucket clearly visible on dark and light backgrounds. Define it as CSS custom properties so Option 3 can reuse it.
- **Island labels instead of dashed boxes.** When zoomed in, draw names at the bbox centres, and drop the dashed rectangles except for the selected island.
- **Accessible legend and keyboard path.**
  - Remove `aria-hidden` from the legend.
  - Make the area dropdown the documented keyboard route.
  - Add an `aria-live` line announcing the selected area.
- **"Locate me" button.** Geolocation highlights the user's ~10 km square and offers its area. The location stays on the device.

## Option 1: Reference geography and smooth zoom (2–4 days)

Keep the hand-written SVG and give it a coastline, labels and real zoom.

**What changes**
- **One static outline file.** `assets/data/shroomhunt/japan-outline.json` holds simplified coastlines and prefecture borders (Natural Earth 1:10m or GSI, simplified with mapshaper to about 60–150 KB). It is generated once by a script in ShroomHunt.
- **Drawn under the cells** with the existing `project()`, plus about 40 place labels.
- **Wheel, drag and pinch zoom.** These change the box state and redraw, clamped to `MIN_SPAN`. Selecting an area animates to its box.
- **Draw cost stays flat.** Cells stay as one `<path>` per colour bucket.

**Impact:** the biggest gain in legibility for the effort, and it could ship before the Okinawa trip.

**Future implications**
- Stays dependency-free and fits the existing tests.
- Tops out at coastlines. Roads, trails, rivers and terrain are out of reach, so moving to Option 3 later throws this work away. The quick wins survive.

## Option 2: The map as a research filter (3–5 days, both repos)

**What changes**
- **Metric toggle** for what the colour shows: records, species, share of genus-only records, or density of "could be found here" species.
- **Filters on the map.** The month and kind filters recolour the map.
- **Search box.** A species or genus lights up the squares where it has been recorded; a place name jumps to an area.
- **Pipeline output.** The pipeline writes a lazily loaded `mesh.json`. Per square it holds 12 monthly counts, a genus-only count and counts by kind, plus a species → squares index. That's about +40–100 KB compressed. The existing `mesh` in `dashboard.json` stays, so first paint is unchanged.

**Impact:** turns the map into a fieldwork pointer, and feeds the planned analysis that clusters genus-only records by place and season.

**Future implications**
- **Species search reveals locations.** It publishes ~10 km presence for every listed species, so gate it behind the review of `sensitive_species.csv`, and consider a minimum count per square.
- **Version the data contract.** Add `schema_version` to `dashboard.json` and `mesh.json`, and have the page refuse or degrade when it sees an unknown version.

## Option 3: MapLibre GL and a real basemap (4–6 days)

**What changes**
- **Library.** MapLibre GL JS (BSD licence, about 250 KB compressed), self-hosted under `assets/vendor/`.
- **Basemap.** OpenFreeMap, or a Japan PMTiles extract on Cloudflare R2. Optional hillshade.
- **Squares become a data layer.** A GeoJSON fill layer is built in the browser from `mesh`, so there is no pipeline change. The selected area is an outline layer.
- **The rest stays put.** The drill-down, `areaAt`, `meshKey`, hash routing and tables stay as they are, and MapLibre's events handle touch.
- **Styles.** A dark style, plus a light variant.

**Impact:** the biggest visual change, and the only option that's usable for finding your way in the field.

**Future implications**
- Adds a dependency and about 300 KB to the first load.
- The tile source is a long-term commitment.
- Attribution must show on the map.
- `project`/`unproject`/`mapSize` and their tests should be deleted, not kept alongside the new renderer.
- Offline use needs PMTiles plus a service worker.

## Comparison and decisions

| Option | Effort | New dependency | Pipeline change | Field use on a phone | Main risk |
| --- | --- | --- | --- | --- | --- |
| Quick wins | ~1 day | None | None | Better (touch works) | None |
| 1. Geography + zoom | 2–4 days | None | None | Usable, coast only | Thrown away if 3 follows |
| 2. Research filter | 3–5 days | None | Yes | Same as the base map | Species search leaks locations before the list review |
| 3. MapLibre basemap | 4–6 days | MapLibre + tile source | None | Good, needs signal | Tile source terms and long-term hosting |

**Decisions needed from David**
1. Is the map a desktop research view or a field tool? This picks Option 1 or Option 3.
2. If Option 3: hosted free tiles or self-hosted PMTiles on R2?
3. Species search: publish ~10 km presence per species after the list review, or limit search to genus and place names?
4. Add `schema_version` now? Recommended yes.
