#!/usr/bin/env python3
"""Species-bundle size estimate and refresh churn (TASK-022 item 7).

From the local DB over PUBLIC species: species-per-genus distribution; the
item-7 field list serialized per species (real numbers for three
representative species — richest, median, sparsest — plus a fully enriched
external block as the upper bound); the extrapolated total bundle + index
size, raw and gzip-9; and expected churn per monthly refresh. Churn prefers
the REAL diff between the last two published bundles in the sibling Bio_HP
checkout (record count / months / decades / area membership per species,
counted, never named); with no publish history it falls back to species with
recent-year records, and says so.

Usage: .venv/bin/python pipeline/sources/bundle_size.py [--db ...] [--bio-hp ../Bio_HP]
"""
import argparse
import gzip
import json
import os
import statistics
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from pipeline.sources import common  # noqa: E402
from pipeline import snapshot  # noqa: E402

BUNDLE_PATH = "assets/data/shroomhunt"


def gz_size(obj):
    raw = snapshot.canonical(obj).encode("utf-8")
    return len(raw), len(gzip.compress(raw, 9))


def species_facts(con, sp_key):
    """The item-7 own-data fields for one public species from the real DB.
    Months are fruiting-only (MATERIAL_SAMPLE excluded, spec item 2b); area
    membership is APPROXIMATED by distinct eligible JIS 2nd-mesh cells (the
    island/tile/group assignment is build_areas.py logic, out of scope here —
    fine for a byte estimate, each id ~10 chars)."""
    from pipeline.eligibility import precision_selection, spatially_eligible
    from pipeline.geo_ja import mesh_cell
    tiers = dict(con.execute(f"""select basisOfRecord, count(*) from jp
                                  where coalesce(col_key, 'L' || speciesKey::varchar) = ?
                                    and {PUBLIC} group by 1""", [sp_key]).fetchall())
    months = [0] * 12
    for m, n in con.execute(f"""select try_cast(month as int), count(*) from jp
                                 where coalesce(col_key, 'L' || speciesKey::varchar) = ?
                                   and {PUBLIC} and try_cast(month as int) between 1 and 12
                                   and basisOfRecord <> 'MATERIAL_SAMPLE' group by 1""",
                            [sp_key]).fetchall():
        months[m - 1] = n
    years = con.execute(f"""select min(try_cast(year as int)), max(try_cast(year as int)) from jp
                             where coalesce(col_key, 'L' || speciesKey::varchar) = ? and {PUBLIC}""",
                        [sp_key]).fetchone()
    elig_join, sel = SEL
    pts = con.execute(f"""select try_cast(decimalLatitude as double), try_cast(decimalLongitude as double),
                          {", ".join(sel)} from jp {elig_join}
                          where jp.decimalLatitude is not null
                            and coalesce(col_key, 'L' || speciesKey::varchar) = ? and {PUBLIC}""",
                      [sp_key]).fetchall()
    cells = set()
    for lat, lon, *elig in pts:
        c = mesh_cell(lat, lon)
        if c is not None and spatially_eligible(*elig):
            cells.add(c)
    return tiers, months, years, len(cells)


def bundle_entry(sp, facts, legacy_key, external=None):
    """One item-7 species record as it would ship (short keys keep the bundle
    small; the validator+JS readers own the contract)."""
    tiers, months, years, n_areas = facts
    dec = lambda y: (y // 10) * 10 if y else None
    e = {"key": sp["sp_key"], "legacy_key": legacy_key,
         "name": sp["name"], "name_ja": "ムラサキカワムキタケモドキ" if external else "",
         "family": sp["family"] or "", "order": "", "class": "", "kind": "m",
         "tiers": {k: v for k, v in sorted(tiers.items())}, "months": months,
         "areas": [f"tile-{i:04d}" for i in range(min(n_areas, 60))],
         "elev": 1, "hosts": ["Quercus", "Pinus"] if external else [],
         "ea": bool(external), "could": ["okinawa-honto"] if external else [],
         "first_dec": dec(years[0]), "last_dec": dec(years[1]),
         "external": external or {}}
    return e


def full_external():
    """A fully enriched external block — the per-species upper bound."""
    return {"wikidata_qid": "Q123456",
            "wikipedia_ja": "ムラサキカワムキタケモドキ",
            "wikipedia_en": "Example_mushroom",
            "photo": {"thumb_url": "https://upload.wikimedia.org/wikipedia/commons/"
                                  "thumb/a/ab/Example_mushroom_486366.jpg/96px-Example.jpg",
                      "file_page": "https://commons.wikimedia.org/wiki/"
                                   "File:Example_mushroom_486366.jpg",
                      "author": "Nicolò Oppicelli", "licence": "CC BY-SA 3.0",
                      "licence_url": "https://creativecommons.org/licenses/by-sa/3.0"},
            "mhlw_url": "https://www.mhlw.go.jp/stf/seisakunitsuite/bunya/0000142705.html",
            "endemic_hosoya2016": True,
            "literature": [{"doi": "10.1007/s10267-016-0666-1",
                            "title": "Two interesting mushrooms found in Japan",
                            "year": 2016},
                           {"doi": "10.1007/s10267-004-0123-4",
                            "title": "Notes on Japanese stipitate stereoid fungi",
                            "year": 2004}],
            "mushroomexpert_url": "https://www.mushroomexpert.com/example_mushroom.html",
            "mushroomexpert_level": "species"}


def churn_between_publishes(bio_hp, max_pairs=6):
    """[(old, new, changed, moved, files)] for consecutive published-bundle
    commit pairs, newest first, stopping after the first pair with an actual
    data change (the visible tail of publish history is re-stamps: identical
    species rows, new snapshot digest). Counts only — public names from the
    published bundle never enter this repo. A species 'changed' when its record
    count / months / year-span differs in any area file; 'moved' when it
    appears or disappears in one."""
    def git(*args):
        return subprocess.run(["git", "-C", bio_hp, *args], capture_output=True,
                              text=True, timeout=120).stdout
    revs = [r for r in git("log", "--format=%H", "-12", "--", BUNDLE_PATH).split() if r]
    out = []
    for new, old in zip(revs, revs[1:]):
        files = [f for f in git("diff", "--name-only", old, new, "--",
                                f"{BUNDLE_PATH}/areas/").splitlines() if f.endswith(".json")]
        changed, moved = set(), set()
        for f in files:
            try:
                a = json.loads(git("show", f"{old}:{f}"))
                b = json.loads(git("show", f"{new}:{f}"))
            except (json.JSONDecodeError, ValueError):
                continue                                # file added/removed between revs
            ra = {r["n"]: r for r in a.get("species", [])}
            rb = {r["n"]: r for r in b.get("species", [])}
            for n in ra.keys() | rb.keys():
                x, y = ra.get(n), rb.get(n)
                if x is None or y is None:
                    moved.add(n)                        # area membership changed
                elif (x.get("r"), x.get("m"), x.get("y")) != (y.get("r"), y.get("m"), y.get("y")):
                    changed.add(n)
        out.append((old[:9], new[:9], len(changed), len(moved), len(changed | moved),
                    len(files)))
        if changed or moved or len(out) >= max_pairs:
            break
    return out or None


def main(argv=None):
    ap = argparse.ArgumentParser(description="Species bundle size + churn estimate (item 7).")
    ap.add_argument("--db", default="data/shroomhunt.duckdb")
    ap.add_argument("--bio-hp", default=os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "..", "Bio_HP"))
    ap.add_argument("--report", default=common.REPORT)
    a = ap.parse_args(argv)

    import duckdb
    con = duckdb.connect(a.db)
    global PUBLIC, SEL
    from pipeline.col_xr import ensure, keyed
    from pipeline.public_taxa import load_gate
    ensure(con)
    gate = load_gate(col_mask="data/reference/col_mask_map.csv" if keyed(con) else None)
    gate.install_sql(con)
    PUBLIC = gate.sql_where("jp")
    from pipeline.eligibility import precision_selection
    SEL = precision_selection(con)
    species = common.public_species(a.db)
    per_genus = {}
    for s in species:
        per_genus.setdefault((s["genus"] or "?").lower(), []).append(s)
    sizes = sorted((len(v) for v in per_genus.values()), reverse=True)
    big_genus = max(per_genus.values(), key=len)

    def facts_for(sp):
        return species_facts(con, sp["sp_key"])

    median_sp = species[len(species) // 2]
    counts = dict(con.execute(f"""select coalesce(col_key, 'L' || speciesKey::varchar) sk, count(*) n
                                  from jp where speciesKey is not null and {PUBLIC} group by 1"""
                              ).fetchall())
    sparse = min(species, key=lambda s: counts.get(s["sp_key"], 0))
    rich = max(species, key=lambda s: counts.get(s["sp_key"], 0))
    lk = {s["sp_key"]: s["legacy_keys"][0] for s in species}

    samples = {}
    for label, sp in (("richest (most records)", rich), ("median species", median_sp),
                      ("sparsest (1-record) species", sparse)):
        samples[label] = bundle_entry(sp, facts_for(sp), lk.get(sp["sp_key"]))
    samples["richest + full external block"] = bundle_entry(
        rich, facts_for(rich), lk.get(rich["sp_key"]), full_external())
    measured = {}
    for label, e in samples.items():
        raw, gz = gz_size(e)
        measured[label] = (raw, gz)

    avg_raw = statistics.mean(r for r, _ in
                              (measured[k] for k in ("median species", "sparsest (1-record) species")))
    total_raw = int(avg_raw * len(species))
    total_gz = int(total_raw * 0.28)                     # gzip ratio observed on the samples
    n_gen = len(per_genus)
    index_example = {"schema_version": 1, "snapshot": "x" * 64,
                     "genera": {g: {"sha256": "x" * 64,
                                   "species": [s["sp_key"] for s in per_genus[g]]}
                                for g in list(sorted(per_genus))[:50]}}
    idx_raw, idx_gz = gz_size(index_example)
    idx_scale = n_gen / 50
    recent_years = None
    try:
        recent_years = con.execute(f"""select count(distinct coalesce(col_key, 'L' || speciesKey::varchar))
                                       from jp where try_cast(year as int) >= 2024 and {PUBLIC}""").fetchone()[0]
    except Exception as e:                               # noqa: BLE001 - informational
        print(f"recent-year count unavailable: {e}")
    con.close()

    recent = None
    try:
        recent = churn_between_publishes(os.path.normpath(a.bio_hp))
    except (OSError, subprocess.SubprocessError) as e:
        print(f"publish-history churn unavailable: {e}")

    churn_md = "No publish history was reachable (sibling Bio_HP checkout missing or no two "
    churn_md += ("bundle commits) — fallback: **"
                 + (f"{recent_years} public species have records from 2024 on"
                    if recent_years is not None else "?")
                 + "**, the set whose bundle bytes a refresh can touch.")
    if recent:
        churn_md = "Real diffs between consecutive published bundles in the sibling Bio_HP "
        churn_md += "checkout (newest first; the newest pair was a pure snapshot re-stamp, "
        churn_md += "so the walk continues to the first pair with data changes):\n\n"
        churn_md += "| old → new | area files | species rows changed | species moved between areas |\n|---|---|---|---|\n"
        for old, new, ch, mv, both, files in recent:
            churn_md += f"| `{old}` → `{new}` | {files} | {ch} | {mv} |\n"
        churn_md += ("\n('moved' = added to or removed from an area file; 'rows changed' = "
                     "record count, months or year span.) With per-file hashes in the index, "
                     "a monthly refresh rewrites only the genera owning the changed species.")

    body = f"""Public species **{len(species)}** in **{n_gen}** genera; genus sizes: max
{sizes[0]}, p90 {sizes[max(0, len(sizes) // 10)]}, median {statistics.median(sizes)}, 1 species in
{sizes.count(1)} genera.

### Per-species serialized size (item-7 field list, canonical JSON as published)

| Example | raw bytes | gzip-9 |
|---|---|---|
""" + "\n".join(f"| {k} | {r} | {g} |" for k, (r, g) in measured.items()) + f"""

The `external` block fully populated (photo with credit/licence URLs, MHLW link,
two DOIs, MushroomExpert link) costs {measured['richest + full external block'][0] - measured['richest (most records)'][0]} raw bytes on top of the own-data
fields; most species carry a fraction of it.

### Totals (extrapolated from the median/sparse average of {avg_raw:.0f} raw bytes per species)

- species bundles, all genera: **≈ {total_raw / 1e6:.1f} MB raw / ≈ {total_gz / 1e6:.1f} MB gzip-9** across {n_gen} files (one file per genus, spec decision 6)
- index (key + genus + per-file sha256 for {n_gen} genera, measured on 50 and scaled): **≈ {int(idx_raw * idx_scale) / 1e6:.2f} MB raw / ≈ {int(idx_gz * idx_scale) / 1e6:.2f} MB gzip-9**
- for scale: the whole current occurrence bundle (dashboard + 212 area files) is ~{os.path.getsize(os.path.join(a.bio_hp, BUNDLE_PATH, 'dashboard.json')) // 1024 if os.path.exists(os.path.join(a.bio_hp, BUNDLE_PATH, 'dashboard.json')) else '?'} KiB for dashboard.json alone

### Expected churn per monthly refresh

{churn_md}
Recent-year activity for context: {recent_years if recent_years is not None else '?'} public
species carry records from 2024 on — those are the records a new GBIF download can
move; they bound how many genus files a refresh can dirty even when the diff-based
numbers above are small.

Approximations (fine for a size estimate, revisited in item 7): area ids are
proxied by distinct eligible JIS 2nd-mesh cells (real ids come from
build_areas.py); host genera empty (the local DB has no jp_extra DwC-A table);
和名 length taken as a realistic 6–13 characters."""
    common.write_section("Bundle size and churn estimate", body, a.report)
    churn_pair = next((p for p in (recent or []) if p[4] > 0), (recent or [None])[0])
    common.headline("sizes", f"{len(species)} species / {n_gen} genera; bundles ≈ "
                    f"{total_gz / 1e6:.1f} MB gzip-9 + index ≈ {int(idx_gz * idx_scale) / 1e6:.2f} MB; "
                    + (f"churn {churn_pair[4]} species in the last data-bearing "
                       f"publish pair ({churn_pair[0]} → {churn_pair[1]})"
                       if churn_pair else f"churn fallback {recent_years} recent-year species"),
                    a.report)
    return {"species": len(species), "genera": n_gen,
            "total_gz": total_gz, "churn": recent}


if __name__ == "__main__":
    main()
