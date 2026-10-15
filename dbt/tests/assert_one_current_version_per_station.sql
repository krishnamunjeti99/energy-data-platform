-- Each station must have exactly one current version, and versions must not
-- overlap: otherwise a day could join to two capacities, or to none.
select station_id, 'current versions' as problem, count(*) as n
from {{ ref('dim_stations') }}
where is_current
group by station_id
having count(*) <> 1

union all

select a.station_id, 'overlapping versions', count(*)
from {{ ref('dim_stations') }} a
join {{ ref('dim_stations') }} b
  on a.station_id = b.station_id
 and a.version < b.version
 and a.valid_from < b.valid_to
 and b.valid_from < a.valid_to
 and a.valid_from < a.valid_to   -- ignore zero-length versions (two changes in one day)
 and b.valid_from < b.valid_to
group by a.station_id
