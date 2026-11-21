-- D7.1  Fungi recorded in neighbouring East Asia but never in Japan (by GBIF accepted species key).
-- Mushroom Observer records act as a filter for conspicuous fungi that people photograph.
-- Each row is a LEAD for an expert to triage into one of:
--   (a) naming mismatch: Japanese records exist under another name, (b) under-recorded in Japan,
--   (c) truly absent. Only a voucher specimen (tier A/B) can turn a lead into a record.
-- TASK-002: the shared protected-taxon gate (pipeline/public_taxa.py) must install its temp
-- tables (sens, sens_names) before this file runs; without them this fails closed.
create or replace table neighbour_candidates as
with jps as (select distinct speciesKey from jp where speciesKey is not null),
jpg as (select genus, count(*) jp_genus_records,
               sum((taxonRank <> 'SPECIES')::int) jp_genus_unresolved from jp where genus is not null group by 1),
mos as (select speciesKey, count(*) mo_records, count(distinct countryCode) mo_countries,
               sum((countryCode in ('RU','CN','KR','KP','TW'))::int) mo_east_asia
        from mo where speciesKey is not null group by 1)
select nb.species, nb.phylum, nb."order", nb.family, nb.genus,
       nb.numberOfOccurrences::int as neighbour_records, nb.iucnRedListCategory as iucn,
       coalesce(mos.mo_records, 0) as mo_records, coalesce(mos.mo_east_asia, 0) as mo_east_asia,
       coalesce(jpg.jp_genus_records, 0) as jp_genus_records,
       coalesce(jpg.jp_genus_unresolved, 0) as jp_genus_unresolved,
       case when coalesce(jpg.jp_genus_records, 0) = 0 then 'genus unrecorded in Japan'
            when jpg.jp_genus_unresolved > 0 then 'Japanese genus-only records exist: check them'
            else 'genus known in Japan' end as triage_hint
from nb left join mos using (speciesKey) left join jpg using (genus)
where nb.taxonRank = 'SPECIES' and nb.taxonomicStatus = 'ACCEPTED'
  and nb.speciesKey not in (select speciesKey from jps)
  and nb.speciesKey::varchar not in (select k from sens)
  and lower(coalesce(nb.species, '')) not in (select name from sens_names);
