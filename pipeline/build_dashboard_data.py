#!/usr/bin/env python3
"""Build the public dashboard summary (dashboard.json) from data/shroomhunt.duckdb.

Public-safety rules (decision D6):
  * No record-level points are published. The map is counts per JIS 2nd mesh (~10 km square).
  * Red List and commercially collected species are left out of the map entirely and are only
    reported as totals. They can still appear by name in species lists, never with a location.

Usage: python3 pipeline/build_dashboard_data.py --out dashboard.json \
         --meta data/raw/gbif/*.json --redlist data/raw/redlist/redlist_fungi_jp.json
Needs: pip install duckdb. Run pipeline/build_db.py and analysis/01_neighbour_candidates.sql first."""
import argparse, csv, json, time, duckdb

ap = argparse.ArgumentParser()
ap.add_argument("--db", default="data/shroomhunt.duckdb")
ap.add_argument("--out", default="data/processed/dashboard.json")
ap.add_argument("--meta", nargs="*", default=[], help="GBIF download .json files (for DOIs)")
ap.add_argument("--redlist", default="data/raw/redlist/redlist_fungi_jp.json")
ap.add_argument("--sensitive", default="data/reference/sensitive_species.csv")
a = ap.parse_args()

c = duckdb.connect(a.db)
c.execute(open("analysis/01_neighbour_candidates.sql").read())

# --- sensitive species keys -------------------------------------------------------------
keys = set()
try:
    keys |= {str(s["speciesKey"]) for s in json.load(open(a.redlist))["species"] if s.get("speciesKey")}
    redlist_n = len(json.load(open(a.redlist))["species"])
except FileNotFoundError:
    redlist_n = 0
names = [r["scientific_name"] for r in csv.DictReader(open(a.sensitive))]
c.execute("create or replace temp table sens_names(name varchar)")
c.executemany("insert into sens_names values (?)", [(n,) for n in names])
keys |= {r[0] for r in c.execute("select distinct speciesKey from jp where species in (select name from sens_names) and speciesKey is not null").fetchall()}
c.execute("create or replace temp table sens(k varchar)")
c.executemany("insert into sens values (?)", [(k,) for k in keys])

one = lambda q: c.execute(q).fetchone()
rows = lambda q: [list(r) for r in c.execute(q).fetchall()]

totals = dict(zip(["records", "species", "genera", "families", "datasets", "georeferenced", "to_species"], one("""
  select count(*), count(distinct speciesKey), count(distinct genus), count(distinct family), count(distinct datasetKey),
         sum((decimalLatitude is not null)::int), sum((taxonRank in ('SPECIES','VARIETY','SUBSPECIES','FORM'))::int) from jp""")))
totals["sensitive_species"] = one("select count(distinct speciesKey) from jp where speciesKey in (select k from sens)")[0]
totals["sensitive_records"] = one("select count(*) from jp where speciesKey in (select k from sens)")[0]
totals["redlist_names"] = redlist_n

evidence = rows("select basisOfRecord, count(*) from jp group by 1 order by 2 desc")
id_depth = rows("""select case when taxonRank in ('SPECIES','VARIETY','SUBSPECIES','FORM') then 'species'
                               when taxonRank='GENUS' then 'genus' else 'higher' end d, count(*) from jp group by 1 order by 2 desc""")
phyla = rows("select coalesce(phylum,'Unknown'), count(*), count(distinct speciesKey) from jp group by 1 order by 2 desc limit 8")
decades = rows("""select (try_cast(year as int)//10)*10 dec, count(*) from jp where try_cast(year as int) between 1850 and 2100
                  group by 1 order by 1""")
families = rows("""select family, count(distinct speciesKey) sp, count(*) n from jp where family is not null
                   group by 1 order by 2 desc limit 15""")

# JIS X 0410 2nd mesh (lat 5', lon 7.5'): code and centre. Sensitive species excluded.
mesh = rows("""
  with p as (select try_cast(decimalLatitude as double) lat, try_cast(decimalLongitude as double) lon, speciesKey
             from jp where decimalLatitude is not null and (speciesKey is null or speciesKey not in (select k from sens))),
  m as (select floor(lat*1.5) p1, floor(lon)-100 u1, floor((lat*1.5 - floor(lat*1.5))*8) q2,
               floor((lon - floor(lon))*8) v2, speciesKey from p where lat between 20 and 46 and lon between 122 and 154)
  select round((p1 + (q2+0.5)/8)/1.5, 4) clat, round(u1 + 100 + (v2+0.5)/8, 4) clon,
         count(*) n, count(distinct speciesKey) sp
  from m group by p1,u1,q2,v2 order by n desc""")

leads = rows("""select species, family, neighbour_records, mo_records, triage_hint from neighbour_candidates
                where mo_records > 0 and species not in (select name from sens_names)
                order by neighbour_records desc, mo_records desc limit 60""")
lead_totals = dict(zip(["neighbour_only", "with_mo", "with_unresolved_jp_genus"], one("""
  select count(*), count(*) filter (where mo_records>0),
         count(*) filter (where mo_records>0 and triage_hint like 'Japanese%') from neighbour_candidates""")))
unresolved = rows("""select genus, family, count(*) n from jp where taxonRank='GENUS' and genus is not null
                     group by 1,2 order by 3 desc limit 15""")
mo_jp = one("select count(*) from jp where datasetKey='d714382d-5890-4234-ae81-696eeb53658a'")[0]

sources = []
for f in a.meta:
    m = json.load(open(f))
    sources.append({"key": m["key"], "doi": m["doi"], "records": m.get("totalRecords"), "created": m.get("created")})

out = {"generated": time.strftime("%Y-%m-%d"), "totals": totals, "evidence": evidence, "id_depth": id_depth,
       "phyla": phyla, "decades": decades, "families": families, "mesh": mesh, "leads": leads,
       "lead_totals": lead_totals, "unresolved_genera": unresolved, "mushroom_observer_japan": mo_jp,
       "sources": sources}
json.dump(out, open(a.out, "w"), separators=(",", ":"), ensure_ascii=False)
print(f"wrote {a.out}: {len(mesh)} mesh cells, {len(leads)} leads, "
      f"{totals['sensitive_species']} sensitive species masked")
