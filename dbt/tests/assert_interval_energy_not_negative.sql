-- A cumulative register can never go backwards. A negative interval means a
-- meter reset, a swapped meter, or a bad read. Returns the offending rows.
select *
from {{ ref('silver_interval_energy') }}
where interval_kwh < 0
