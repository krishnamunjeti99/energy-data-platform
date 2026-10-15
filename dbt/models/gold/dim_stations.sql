-- Station dimension with full history (SCD Type 2), built from the snapshot.
--
-- One row per station per version. valid_from / valid_to are dates
-- (valid_to exclusive), so a fact on reading_date joins to exactly one version:
--     reading_date >= valid_from and reading_date < valid_to
--
-- The first version of each station is treated as valid since forever:
-- the snapshot only started recording when it was first run, but the data
-- loaded before that should still find a matching station.

with versions as (

    select
        station_id,
        station_name,
        source,
        capacity_mw,
        dbt_valid_from,
        dbt_valid_to,
        row_number() over (partition by station_id order by dbt_valid_from) as version
    from {{ ref('stations_snapshot') }}

)

select
    station_id,
    version,
    station_name,
    source,
    capacity_mw,
    case when version = 1 then date '1900-01-01'
         else dbt_valid_from::date end                    as valid_from,
    coalesce(dbt_valid_to::date, date '9999-12-31')       as valid_to,
    dbt_valid_to is null                                  as is_current
from versions
