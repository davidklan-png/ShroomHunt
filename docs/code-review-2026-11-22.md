# ShroomHunt / Bio_HP code review (2026-11-22)

**Reviewed:** ShroomHunt `main` at a0c988a (month-0 withholding dropped) and Bio_HP `main` at 0dd6e7f (PR #57 merged). The live site serves the same JS (`readResearch`, Name search, `member_names` present).

**Tests:**
- ShroomHunt: 121 pass. 2 error because `SeededTaxonNames` reads the private raw Red List (`data/raw/redlist/redlist_fungi_jp.json`).
- Bio_HP fungi tests: 119/119 pass.

**Overall:** the safety architecture is strong.
- One shared gate (`public_taxa.py`) feeds every public output.
- Fail-closed reference inputs.
- A validator that re-runs the gate before any PR.
- Snapshot-joined bundles.
- Consistent HTML escaping in the dashboard JS.

The issues below are mostly *seams*: places where a later decision quietly invalidated an earlier safeguard, or where documented behaviour isn't actually wired up.

---

## David's decisions (2026-11-22)
- **Masking is species-level only, everywhere,** for both the Red List and the commercial list (`taxon_mask.csv`, kept). No genus is masked or excluded, including from Name search. Example: *Ganoderma lucidum* is masked; other *Ganoderma* species and genus-rank *Ganoderma* records publish and stay searchable.
- **Small genera:** no extra rule. Only a species-level identification of a masked taxon is masked.
- **Event independence:** no multi-dataset requirement for now. The current ≥3-event support gate stands.

---

## P1: Protection, revised to fit the decisions

### 1. Labels and synonyms in the name tables
Genus search for *Ganoderma*, *Tricholoma*, *Grifola* and *Tuber* stays, per the decision. Two smaller fixes keep the species-level rule accurate:

- **Mask synonyms of masked species (same species, other name).** `taxon_mask.csv` has no aliases. East Asian reishi is widely recorded as *Ganoderma lingzhi* / *G. sichuanense*, the current names for what was called *G. lucidum* in Japan, so those records publish unmasked. Add them as aliases on the *G. lucidum* row. Check the other commercial rows the same way (for example, *Tricholoma nauseosum* for matsutake, in case GBIF keeps it as a separate accepted name).
- **Don't label a genus with a masked species' common name.** `taxon_names.csv` uses species names as genus labels/aliases:
  - `matsutake|マツタケ` → Tricholoma
  - `マンネンタケ`, `reishi`, `lingzhi` → Ganoderma
  - `maitake / マイタケ` → Grifola

  Searching "matsutake" lands on genus *Tricholoma* areas, which implies matsutake is there. Use genus-level labels instead (for example, Ganoderma: `マンネンタケ属`, "ganoderma"; Tricholoma: `キシメジ属`, "knights"). The genus results themselves are unchanged. **David to confirm.**
- **Fix the stale `taxon_names.csv` header** ("Masked genera are still dropped at build time" is no longer true).

### 2. Withdrawn
The small-genera rule was rejected; species-level only.

---

## P2: Research correctness

### 3. Phenology is distorted by DNA sampling (matters for the Okinawa trip)
Okinawa Honto months are `[16, 6961, 31, 61, 31, 24, 54, 49, 35, 43, 2884, 125]`. The February and November spikes are DNA sampling events (Oku/Yonaha/Uruma), not fruiting.

`top_localities` was withdrawn for safety, so `samplingNote()` in the dashboard JS is now dead code and **no bias warning shows anywhere**.

**Fix:**
- Compute area `months` and research month slices from fruiting evidence. Exclude `basisOfRecord = MATERIAL_SAMPLE` (EVID `d`), or ship `months` split by evidence `[s, o, d]`.
- Add a governed, non-locational field such as `dominant_source: [dataset_title, share_pct]`. Dataset titles are public metadata, not locations. The UI shows "61% from one DNA survey" when the share is ≥ 30%.

### 4. Withdrawn
Event independence: no multi-dataset requirement for now.

### 5. Deduplication is a no-op; cross-dataset duplicates are counted twice
Within one GBIF SIMPLE_CSV download, `gbifID` is unique, so the dedupe never fires. The real duplicates are the same specimen published by two datasets (for example, S-Net plus another aggregator).

**Fix:** dedupe on normalised `(institutionCode, catalogNumber)` where present, or use GBIF's occurrence clustering (`isInCluster`). Log the aggregate count removed.

---

## P3: Engineering hygiene

6. **The `--previous` differencing guard is never used.** `publish-dashboard.yml` doesn't pass `--previous`. The guard also compares only with the immediately previous file, so a cell drops for one publish and then reappears (flicker), which defeats it. Either delete it (consistent with the band-arithmetic reasoning in a0c988a), or wire it to a ledger of all published bands. Also update the stale docstrings: the month-0 "10 of 12" rule and "These drops happen before the month-0 suppression count".
7. **ShroomHunt has no CI.** Add a `pytest` workflow on push/PR and a `pytest` step before building in `publish-dashboard.yml`. Make `SeededTaxonNames` use a fixture Red List; it should not read `data/raw`.
8. **`kind()` is duplicated** between `build_areas.py` and `build_mesh_research.py`, identical today. Wrap `build_areas.py` in `main()` and move the classifier into a `taxonomy.py` module that both import.
9. **Bio_HP CI tests only owned fixtures,** so a data PR can't break CI even if the reader would reject it. Add a smoke test that loads the real `assets/data/shroomhunt/*.json`, checks snapshot joins and runs the readers. Longer term, generate one JSON Schema from the §6 contract and use it in both the Python validator and the JS tests.
10. **The "Where/Habitat" column is blank for every island and tile** (`has_habitat` false, `h/l/el` empty). Hide the column until a governed field exists. A good candidate is a host-plant genus taken from a controlled vocabulary, which carries no location.
11. **Stale docs:**
    - The README still describes "Forage Season Finder" and Mushroom Observer setup.
    - `app/index.html` is the legacy app.
    - `docs/architecture-decisions.md` still marks D2/D6 as "Proposed" even though they're applied. Record the 2026-11-22 species-level-only ruling there.
12. **Bundle churn:** every refresh restamps the snapshot in all ~211 area files (8.7 MB), so each data PR rewrites the whole bundle in Bio_HP history, and there were 3 today. Acceptable at a monthly cadence. If PRs stay frequent, keep the snapshot in `index.json` plus a manifest of per-file hashes.
