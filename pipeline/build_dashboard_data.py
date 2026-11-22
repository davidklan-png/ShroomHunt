#!/usr/bin/env python3
"""Build the public dashboard summary (dashboard.json) from data/shroomhunt.duckdb.

Public-safety rules (decision D6):
  * No record-level points are published. The map is counts per JIS 2nd mesh (~10 km square).
  * Red List and commercially collected species are left out of the map entirely and are only
    reported as totals. They can still appear by name in species lists, never with a location.

Usage: python3 pipeline/build_dashboard_data.py --out dashboard.json \
         --meta data/raw/gbif/*.json --redlist data/raw/redlist/redlist_fungi_jp.json
Needs: pip install duckdb. Run pipeline/build_db.py and analysis/01_neighbour_candidates.sql first."""
import argparse, json, os, sys, time, duckdb
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from geo_ja import cell_center, mesh_cell  # noqa: E402
from public_taxa import load_gate  # noqa: E402
from eligibility import precision_selection, spatially_eligible  # noqa: E402
from snapshot import write_json  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--db", default="data/shroomhunt.duckdb")
ap.add_argument("--out", default="data/processed/dashboard.json")
ap.add_argument("--meta", nargs="*", default=[], help="GBIF download .json files (for DOIs)")
ap.add_argument("--redlist", default="data/raw/redlist/redlist_fungi_jp.json")
ap.add_argument("--mask", default="data/reference/taxon_mask.csv")
ap.add_argument("--coverage", default="data/reference/redlist_coverage.csv")
ap.add_argument("--exceptions", default="data/reference/match_exceptions.csv")
a = ap.parse_args()

c = duckdb.connect(a.db)
# Shared protected-taxon gate (TASK-002): install before the neighbour SQL, which uses its tables.
gate = load_gate(mask=a.mask, coverage=a.coverage, exceptions=a.exceptions, redlist=a.redlist)
gate.install_sql(c)
c.execute(open("analysis/01_neighbour_candidates.sql").read())

one = lambda q: c.execute(q).fetchone()
rows = lambda q: [list(r) for r in c.execute(q).fetchall()]
public = gate.sql_where("jp")
not_public = f"not {public}"

totals = dict(zip(["records", "species", "genera", "families", "datasets", "georeferenced", "to_species"], one("""
  select count(*), count(distinct speciesKey), count(distinct genus), count(distinct family), count(distinct datasetKey),
         sum((decimalLatitude is not null)::int), sum((taxonRank in ('SPECIES','VARIETY','SUBSPECIES','FORM'))::int) from jp""")))
totals["sensitive_species"] = one(f"select count(distinct speciesKey) from jp where {not_public}")[0]
totals["sensitive_records"] = one(f"select count(*) from jp where {not_public}")[0]
totals["redlist_names"] = gate.redlist_names

evidence = rows("select basisOfRecord, count(*) from jp group by 1 order by 2 desc, 1")
id_depth = rows("""select case when taxonRank in ('SPECIES','VARIETY','SUBSPECIES','FORM') then 'species'
                               when taxonRank='GENUS' then 'genus' else 'higher' end d, count(*) from jp group by 1 order by 2 desc, 1""")
phyla = rows("select coalesce(phylum,'Unknown'), count(*), count(distinct speciesKey) from jp group by 1 order by 2 desc, 1 limit 8")
decades = rows("""select (try_cast(year as int)//10)*10 as "dec", count(*) from jp
                  where try_cast(year as int) between 1850 and 2100 group by 1 order by 1""")
families = rows(f"""select family, count(distinct speciesKey) sp, count(*) n from jp
                   where family is not null and {public} group by 1 order by 2 desc, 1 limit 15""")

# JIS X 0410 2nd mesh (lat 5', lon 7.5') via the canonical geometry in geo_ja
# (TASK-004): integer cell keys here match build_areas.py cell ownership exactly.
# Sensitive species excluded, and the shared source-precision eligibility applies
# (TASK-006): a cell held only by flagged/coarsened records appears in no spatial
# output at all, this one included. MeshRow = [center_lat, center_lon, records, species].
elig_join, sel = precision_selection(c)
pts = c.execute(f"""select try_cast(jp.decimalLatitude as double), try_cast(jp.decimalLongitude as double), jp.speciesKey,
                    {", ".join(sel)}
                    from jp {elig_join} where jp.decimalLatitude is not null and {public}""").fetchall()
cells = defaultdict(lambda: [0, set()])            # (row, col) -> [records, species keys]
for lat, lon, sk, unc, prec, iss, genz, held in pts:
    cell = mesh_cell(lat, lon)
    if cell is None or not spatially_eligible(unc, prec, iss, genz, held):
        continue                                   # non-finite, outside the envelope, or ineligible
    hit = cells[cell]
    hit[0] += 1
    if sk is not None: hit[1].add(sk)
mesh = [[*cell_center(row, col), n, len(sp)] for (row, col), (n, sp) in cells.items()]
mesh.sort(key=lambda r: (-r[2], r[0], r[1]))       # records desc, then place: deterministic

leads = rows("""select species, family, neighbour_records, mo_records, triage_hint from neighbour_candidates
                where mo_records > 0 order by neighbour_records desc, mo_records desc, species limit 60""")
lead_totals = dict(zip(["neighbour_only", "with_mo", "with_unresolved_jp_genus"], one("""
  select count(*), count(*) filter (where mo_records>0),
         count(*) filter (where mo_records>0 and triage_hint like 'Japanese%') from neighbour_candidates""")))
unresolved = rows("""select genus, family, count(*) n from jp where taxonRank='GENUS' and genus is not null
                     group by 1,2 order by 3 desc, 1 limit 15""")  # genus names publish (species-only masking)
mo_jp = one("select count(*) from jp where datasetKey='d714382d-5890-4234-ae81-696eeb53658a'")[0]

sources = []
for f in a.meta:
    m = json.load(open(f))
    sources.append({"key": m["key"], "doi": m["doi"], "records": m.get("totalRecords"), "created": m.get("created")})

# §6 v1 Dashboard shape (TASK-006): schema_version 1 everywhere; snapshot stays
# empty here and is stamped over the whole staging bundle by pipeline/snapshot.py.
out = {"schema_version": 1, "snapshot": "", "generated": time.strftime("%Y-%m-%d"),
       "totals": totals, "evidence": evidence, "id_depth": id_depth,
       "phyla": phyla, "decades": decades, "families": families, "mesh": mesh, "leads": leads,
       "lead_totals": lead_totals, "unresolved_genera": unresolved, "mushroom_observer_japan": mo_jp,
       "sources": sources}
write_json(a.out, out)
print(f"wrote {a.out}: {len(mesh)} mesh cells, {len(leads)} leads, "
      f"{totals['sensitive_records']} records masked/quarantined "
      f"({one('select count(*) from jp where speciesKey::varchar in (select k from sens)')[0]} by Red List key), "
      f"{totals['sensitive_species']} masked species")
