{{ config(materialized='view') }}

-- Cleaned meter reads: one row per meter per 15-minute block.
-- Bronze can receive the same read more than once (a meter re-sending after a
-- comms retry). We keep the first copy that arrived.

with ranked as (

    select
        read_id,
        meter_id,
        station_id,
        meter_type,
        reading_ts,
        kwh_register,
        kw_avg,
        ingested_at,
        row_number() over (
            partition by meter_id, reading_ts
            order by read_id
        ) as copy_number
    from {{ source('bronze', 'meter_reads') }}

)

select
    -- readable, deterministic key: meter + block end in UTC
    meter_id || '@' || to_char(reading_ts at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI"Z"') as read_key,
    read_id,
    meter_id,
    station_id,
    meter_type,
    reading_ts,
    kwh_register,
    kw_avg,
    ingested_at
from ranked
where copy_number = 1
