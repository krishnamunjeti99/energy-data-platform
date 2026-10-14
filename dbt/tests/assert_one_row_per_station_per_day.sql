select reading_date, station_id, count(*) as row_count
from {{ ref('gold_daily_generation') }}
group by reading_date, station_id
having count(*) > 1
