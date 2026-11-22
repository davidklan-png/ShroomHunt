-- D7.1  Fungi recorded in neighbouring East Asia but never in Japan (by accepted species key).
-- Mushroom Observer records act as a filter for conspicuous fungi that people photograph.
-- Each row is a LEAD for an expert to triage into one of:
--   (a) naming mismatch: Japanese records exist under another name, (b) under-recorded in Japan,
--   (c) truly absent. Only a voucher specimen (tier A/B) can turn a lead into a record.
-- TASK-002: the shared protected-taxon gate (pipeline/public_taxa.py) must install its temp
-- tables (sens, sens_names) before this file runs; without them this fails closed.
-- TASK-018: species identity is the COL XR key (coalesce(col_key, 'L' || speciesKey)); two
-- backbone species lumped under one COL id group to ONE lead (occurrence counts summed).
-- The col_* columns are guaranteed on jp/nb/mo by col_xr.ensure() before this runs.
-- jp must stay unqualified — pipeline/build_dashboard_data.py shadows it with a deduped
-- temp copy (TASK-016); a schema-qualified reference would silently read the raw table.
-- tests/test_dashboard_dedup.py guards this.
create or replace table neighbour_candidates as
with jps as (select distinct coalesce(col_key, 'L' || speciesKey::varchar) as spk
             from jp where speciesKey is not null),
jpg as (select coalesce(col_genus, genus) as genus, count(*) jp_genus_records,
               sum((taxonRank <> 'SPECIES')::int) jp_genus_unresolved
        from jp where genus is not null group by 1),
mos as (select coalesce(col_key, 'L' || speciesKey::varchar) as spk, count(*) mo_records,
               count(distinct countryCode) mo_countries,
               sum((countryCode in ('RU','CN','KR','KP','TW'))::int) mo_east_asia
        from mo where speciesKey is not null group by 1),
nb0 as (select coalesce(col_key, 'L' || speciesKey::varchar) as spk, col_key,
               coalesce(col_name, species) as species, coalesce(col_phylum, phylum) as phylum,
               coalesce(col_order, "order") as "order", coalesce(col_family, family) as family,
               coalesce(col_genus, genus) as genus,
               try_cast(numberOfOccurrences as bigint) as n_occ, iucnRedListCategory as iucn,
               (speciesKey::varchar in (select k from sens)) as legacy_masked,
               (lower(coalesce(species, '')) in (select name from sens_names)) as name_masked
        from nb where taxonRank = 'SPECIES' and taxonomicStatus = 'ACCEPTED'
                  and speciesKey is not null),
nbk as (select spk, col_key, species, phylum, "order", family, genus, max(iucn) as iucn,
               sum(n_occ) as neighbour_records,
               bool_or(legacy_masked) as legacy_masked, bool_or(name_masked) as name_masked
        from nb0 group by 1,2,3,4,5,6,7)
select nbk.species, nbk.phylum, nbk."order", nbk.family, nbk.genus,
       nbk.neighbour_records::int as neighbour_records, nbk.iucn as iucn,
       coalesce(mos.mo_records, 0) as mo_records, coalesce(mos.mo_east_asia, 0) as mo_east_asia,
       coalesce(jpg.jp_genus_records, 0) as jp_genus_records,
       coalesce(jpg.jp_genus_unresolved, 0) as jp_genus_unresolved,
       case when coalesce(jpg.jp_genus_records, 0) = 0 then 'genus unrecorded in Japan'
            when jpg.jp_genus_unresolved > 0 then 'Japanese genus-only records exist: check them'
            else 'genus known in Japan' end as triage_hint,
       nbk.col_key
from nbk left join mos using (spk) left join jpg using (genus)
where nbk.spk not in (select spk from jps)
  and coalesce(nbk.col_key, '') not in (select k from sens)
  and not nbk.legacy_masked and not nbk.name_masked;
