{{
    config(
        materialized='incremental',
        unique_key='read_key',
        incremental_strategy='merge',
        on_schema_change='fail'
    )
}}

-- Interval energy per meter, derived from the cumulative register.
--
-- A meter reports a running total (kwh_register), not the energy of each block.
-- Energy for a block = this read's register - the previous read's register.
--
-- Because the register is cumulative, a missing read loses no energy: the next
-- read that does arrive covers the gap (blocks_covered > 1). We keep that energy
-- and flag the row, rather than dropping or guessing.
--
-- Incremental: each run re-processes only the last `silver_lookback_days` of
-- reads and merges them on read_key. The look-back lets lag() see the previous
-- read across the boundary, and absorbs reads that arrive a little late (a late
-- read changes the interval of the read after it, which is re-merged).
-- Reads arriving later than the look-back need: dbt build --full-refresh

with reads as (

    select * from {{ ref('stg_meter_reads') }}
    {% if is_incremental() %}
    where reading_ts > (
        select max(reading_ts) - interval '{{ var("silver_lookback_days") }} days'
        from {{ this }}
    )
    {% endif %}

),

with_previous as (

    select
        *,
        lag(reading_ts)   over w as prev_reading_ts,
        lag(kwh_register) over w as prev_kwh_register
    from reads
    window w as (partition by meter_id order by reading_ts)

)

select
    read_key,
    meter_id,
    station_id,
    meter_type,
    reading_ts,
    reading_ts - interval '15 minutes'                                     as block_start_ts,
    ((reading_ts - interval '15 minutes') at time zone 'Asia/Kolkata')::date as reading_date,
    kwh_register,
    kwh_register - prev_kwh_register                                       as interval_kwh,
    (extract(epoch from reading_ts - prev_reading_ts) / 900)::int          as blocks_covered,
    reading_ts - prev_reading_ts > interval '15 minutes'                   as is_gap_fill,
    kw_avg
from with_previous
-- the first read in the window has nothing to subtract from: on a full build
-- that's each meter's very first read; on an incremental run it's a read that
-- is already in the table from an earlier run
where prev_reading_ts is not null
