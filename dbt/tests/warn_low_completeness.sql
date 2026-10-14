-- Data-quality warning, not a failure: days where a station delivered fewer
-- than 95% of expected reads. These are the cases the DQ triage agent will
-- investigate later.
{{ config(severity='warn') }}

select reading_date, station_id, reads_expected, reads_received, completeness_pct
from {{ ref('gold_daily_generation') }}
where completeness_pct < 95
