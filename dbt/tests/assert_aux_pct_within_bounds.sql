-- Business rule: auxiliary consumption above 10% of generation is not
-- physically plausible for these plants and points to a metering problem.
select reading_date, station_id, aux_pct
from {{ ref('gold_daily_generation') }}
where generation_kwh > 0
  and (aux_pct < 0 or aux_pct > 10)
