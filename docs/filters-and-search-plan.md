# ShroomHunt: lichen masking, honest filters, name search

24 November 2026 JST. Planner-owned design doc: workers read it, they don't edit it.
Follows Bio_HP PR #70 (pre-contact fixes, merged and live).

## 1. David's decisions (2026-11-24)

| # | Decision | Status |
|---|---|---|
| M1 | 5th Red List **lichens** are masked like fungi: species level only. | Decided |
| F1 | **The month always drives the map.** The "Research filters" button goes. A square with no published estimate for the active filter shows as a hatched outline, never as empty. | Decided |
| F2 | **Kind chips come back:** すべて / きのこ類 / 地衣類 / 微小菌・DNA (pipeline kinds all / m / l / o). | Decided |
| F3 | Colour the map by genus-only share: **not now**. | Decided |
| S1 | **Name search, Phase 1 (Bio_HP only):** scientific-name search over every published species and genus, with map outlines. | Decided |
| S2 | **Name search, Phase 2 (ShroomHunt + Bio_HP):** a name index adds 和名, family and synonyms. | Decided |
| S3 | **Species by square (ShroomHunt + Bio_HP):** a named species lights up its own ~10 km squares; a tapped square lists its species. One small file, public species only, single-record pairs included. | Decided 2026-11-25 |

The masking policy is unchanged. Research covers every kind of fungus. For practical reasons
only (over-collection), species on the 5th Red List or on `taxon_mask.csv` are masked at
species level. No genus is ever masked or excluded, including from search.

S1, David's test of success (2026-11-24): a researcher types the scientific name of a
specific, obscure species and the search filters down to that one name, as long as it is not
masked. Rank wins over popularity. A masked name filters to nothing and is never confirmed
or denied. 和名 coverage is not a blocker for S2: 278 of 12,120 public species carry one
today.

S3 (2026-11-25), after David was told that 69% of species–square pairs rest on one
record: `species/squares.json` lists, per ~10 km square of the map, the `summary.json`
rows recorded there. A species sits in a square only through a record that passes the
masking gate and the map's source-precision rule; masked species have no summary row, so
they cannot appear. No species allowlist and no taxon-reviewer sign-off: this supersedes
`map-improvement-plan.md` §4 item 3 on those two points and on the several-records
rule. The index stays removable: Bio_HP works without the file (TASK-017).

## 2. Problems on the live page

1. **The research view silently drops squares.** It swaps 2,516 all-time squares for 649.
   Its support gate is ≥10 records and ≥3 events per cell, so below-threshold squares vanish
   and read as "nothing here".
2. **One colour means two things.** The research view's lightest colour means 10–24, while
   on the normal map the same colour means 1–9.
3. **With research off, month chips change the lists but not the map.**
4. **The legend promises "month & kind", but kind can't be chosen** (the slicer was removed
   in Bio_HP PR #54).
5. **Name search reaches 3 genera** (the reviewed `taxon_names.csv` rows). Meanwhile
   `species/summary.json` (12,120 public species, all names, TASK-025) and 3,049 genus files
   (和名, island/tile `area_ids`, `could_be_found_area_ids`, fruiting months) are already
   published and unused by search.
6. **A place search with no match** (波照間) still shows the masking note, which does not
   apply.
7. **Red-listed lichens are published**, including CR species, because only the fungi PDF
   feeds masking.

## 3. Tasks, in order

| Order | Repo / task | Scope | Depends on |
|---|---|---|---|
| 1 | ShroomHunt `handoff/TASK-026-redlist-lichens.md` | Parse the lichen PDF into the masking input; then Fetch data → Publish dashboard | — |
| 2 | Bio_HP `handoff/TASK-012-lichen-protection-text.md` | Protection wording and both Red List links | ShroomHunt TASK-026 published |
| 3 | Bio_HP `handoff/TASK-013-honest-filters.md` | One map rule, hatching, distinct ramp, "表示中" line, kind chips | — |
| 4 | Bio_HP `handoff/TASK-014-name-search-phase1.md` | summary.json-backed Latin search, genus and species panels, map outlines | TASK-013 merged (shares the "表示中" line) |
| 5 | ShroomHunt `handoff/TASK-027-species-name-index.md` | Publish `species/names.json` (和名, family, synonyms) | TASK-026 merged |
| 6 | Bio_HP `handoff/TASK-015-name-search-phase2.md` | 和名 / family / synonym / fuzzy search | TASK-014 merged, TASK-027 published |

One PR per task, tests first, Codex review on each PR. `handoff/verify.sh` defines
"green" in each repo.

## 4. Constraints (every task)

- **Data protection:**
  - no record-level points;
  - species-level masking only;
  - masked names never appear in diffs, PR text or public files (counts only; names go
    to the gitignored private diff);
  - the browser fetches only published bundles.
- **Bio_HP site rules:**
  - keep the CSS system and tokens (`dashboard-layout-plan.md` §4). If a distinct ramp
    truly needs a new token, stop and ask David;
  - one include serves EN and JA with full parity (`T` table);
  - 44 px targets, one polite live region, keyboard operation, 320 px check, both themes;
  - bump the CSS `?v=` stamp.
- **Existing behaviour must not regress:**
  - `selectArea()` stays the single selection path;
  - `#area-` / `#species-` deep links and history keep working;
  - street map, locate and the snapshot guards keep working.
