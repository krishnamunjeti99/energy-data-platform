-- Daily energy balance per station: the table the dashboard and the analyst
-- agent read from.

with intervals as (

    select * from {{ ref('silver_interval_energy') }}

),

stations as (

    select * from {{ ref('stations') }}

),

meters_per_station as (

    select station_id, count(*) as meter_count
    from {{ ref('meters') }}
    group by station_id

),

daily as (

    select
        reading_date,
        station_id,
        sum(interval_kwh) filter (where meter_type = 'GEN') as generation_kwh,
        sum(interval_kwh) filter (where meter_type = 'AUX') as auxiliary_kwh,
        count(*)                                            as reads_received,
        count(*) filter (where is_gap_fill)                 as gap_fills
    from intervals
    group by reading_date, station_id

)

select
    d.reading_date,
    d.station_id,
    s.station_name,
    s.source,
    s.capacity_mw,
    round(d.generation_kwh, 3)                                          as generation_kwh,
    round(d.auxiliary_kwh, 3)                                           as auxiliary_kwh,
    round(d.generation_kwh - d.auxiliary_kwh, 3)                        as net_export_kwh,
    round(100 * d.auxiliary_kwh / nullif(d.generation_kwh, 0), 2)       as aux_pct,
    round(100 * d.generation_kwh / (s.capacity_mw * 1000 * 24), 2)      as capacity_factor_pct,
    m.meter_count * 96                                                  as reads_expected,
    d.reads_received,
    round(100.0 * d.reads_received / (m.meter_count * 96), 2)           as completeness_pct,
    d.gap_fills
from daily d
join stations s           on s.station_id = d.station_id
join meters_per_station m on m.station_id = d.station_id
