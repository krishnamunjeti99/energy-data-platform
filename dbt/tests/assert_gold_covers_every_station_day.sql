-- Every station-day present in silver must reach gold. Guards the joins in
-- gold_daily_generation: a station with no matching dim_stations version (or no
-- meters) would otherwise disappear silently.
select distinct reading_date, station_id
from {{ ref('silver_interval_energy') }}

except

select reading_date, station_id
from {{ ref('gold_daily_generation') }}
