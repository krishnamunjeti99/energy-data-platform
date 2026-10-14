"""
Energy meter read generator  ->  bronze.meter_reads

Simulates 15-minute interval reads from energy meters at three fictional
generating stations (hydro, solar, wind) and bulk-loads them into the bronze
layer of the warehouse with Postgres COPY.

Each read looks like what a real meter reports:
  - kwh_register : the cumulative energy register (it only ever goes up)
  - kw_avg       : average power over the 15-minute block
  - reading_ts   : the END of the block (the 00:00-00:15 block is stamped 00:15)

Bronze is raw and append-only. To make the later layers do real work, the
generator injects the same problems real meter data has:
  - dropped reads    (comms glitch: a block is missing, the register keeps counting)
  - comms outages    (a run of consecutive missing blocks)
  - duplicate reads  (the same read is sent twice)

Usage (inside Docker, recommended):
    docker compose run --rm generator --days 30
Usage (locally, from the repo root, reads .env):
    python generator/generate_meter_reads.py --days 30

Run it again with no --start and it continues from the last loaded day, with
registers carrying on from their last values, so it also works as a daily
incremental load.
"""

from __future__ import annotations

import argparse
import math
import os
import random
import sys
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
BLOCK = timedelta(minutes=15)
BLOCKS_PER_DAY = 96

# Anomaly rates (used unless --clean is passed)
P_DROP = 0.004            # chance a single read goes missing
P_DUPLICATE = 0.003       # chance a read is sent twice
P_OUTAGE_PER_DAY = 0.05   # chance a meter has a comms outage on a given day
OUTAGE_BLOCKS = (2, 8)    # an outage lasts this many consecutive blocks


# --------------------------------------------------------------------------- #
# Plant and meter configuration (fictional stations)
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Station:
    station_id: str
    name: str
    source: str           # hydro | solar | wind
    capacity_mw: float
    aux_base_kw: float    # constant auxiliary load (cooling, lighting, controls)
    aux_frac: float       # auxiliary load that scales with generation


@dataclass(frozen=True)
class Meter:
    meter_id: str
    station_id: str
    meter_type: str       # GEN = generator export meter, AUX = auxiliary consumption
    capacity_share: float # share of station capacity behind a GEN meter


STATIONS = [
    Station("STN-HYD-01", "Riverbend Hydro", "hydro", 120.0, 900.0, 0.012),
    Station("STN-SOL-01", "Sunfield Solar",  "solar",  50.0, 150.0, 0.006),
    Station("STN-WND-01", "Windridge Wind",  "wind",   75.0, 250.0, 0.008),
]

METERS = [
    Meter("MTR-HYD-GEN-01", "STN-HYD-01", "GEN", 0.5),
    Meter("MTR-HYD-GEN-02", "STN-HYD-01", "GEN", 0.5),
    Meter("MTR-HYD-AUX-01", "STN-HYD-01", "AUX", 0.0),
    Meter("MTR-SOL-GEN-01", "STN-SOL-01", "GEN", 1.0),
    Meter("MTR-SOL-AUX-01", "STN-SOL-01", "AUX", 0.0),
    Meter("MTR-WND-GEN-01", "STN-WND-01", "GEN", 1.0),
    Meter("MTR-WND-AUX-01", "STN-WND-01", "AUX", 0.0),
]

STATION_BY_ID = {s.station_id: s for s in STATIONS}


# --------------------------------------------------------------------------- #
# Generation profiles: fraction of capacity (0..1) for one 15-minute block
# --------------------------------------------------------------------------- #

def hydro_fraction(hour: float, day_factor: float, rng: random.Random) -> float:
    """Peaking hydro: a base load, ramped up for the morning and evening peaks."""
    base = 0.50
    morning = 0.20 * math.exp(-((hour - 8.5) ** 2) / 4.0)
    evening = 0.38 * math.exp(-((hour - 19.5) ** 2) / 3.0)
    return (base + morning + evening) * day_factor * rng.uniform(0.97, 1.03)


def solar_fraction(hour: float, day_factor: float, rng: random.Random) -> float:
    """Solar bell curve between sunrise and sunset, scaled by the day's cloud cover."""
    sunrise, sunset = 6.25, 18.5
    if hour <= sunrise or hour >= sunset:
        return 0.0
    shape = math.sin(math.pi * (hour - sunrise) / (sunset - sunrise)) ** 1.3
    return 0.85 * shape * day_factor * rng.uniform(0.92, 1.05)


def wind_fraction(state: float, hour: float, rng: random.Random) -> float:
    """Persistent random walk (wind changes gradually) with a mild afternoon lift."""
    diurnal = 0.06 * math.sin(math.pi * (hour - 9) / 12)
    return state + diurnal


def step_wind_state(state: float, rng: random.Random) -> float:
    # AR(1) process pulled toward 0.38 capacity factor
    return 0.92 * state + 0.08 * 0.38 + rng.gauss(0, 0.035)


# --------------------------------------------------------------------------- #
# Read generation
# --------------------------------------------------------------------------- #

def generate_reads(start: date, days: int, start_registers: dict[str, float],
                   rng: random.Random, inject_anomalies: bool):
    """Return (rows, stats). rows are tuples in COPY column order."""
    registers = dict(start_registers)
    wind_state = 0.38
    stats = Counter()
    rows = []

    # Pre-plan comms outages: (meter_id, day_index) -> set of block indexes
    outage_blocks: dict[tuple[str, int], set[int]] = {}
    if inject_anomalies:
        for m in METERS:
            for d in range(days):
                if rng.random() < P_OUTAGE_PER_DAY:
                    length = rng.randint(*OUTAGE_BLOCKS)
                    first = rng.randint(0, BLOCKS_PER_DAY - length)
                    outage_blocks[(m.meter_id, d)] = set(range(first, first + length))

    for d in range(days):
        day = start + timedelta(days=d)
        # Day-level conditions: water availability, cloud cover
        hydro_day = rng.uniform(0.85, 1.0)
        solar_day = rng.choice([1.0, 1.0, 0.95, 0.85, 0.6, 0.4])

        for b in range(BLOCKS_PER_DAY):
            block_start = datetime.combine(day, time(0, 0), tzinfo=IST) + b * BLOCK
            block_end = block_start + BLOCK
            hour = block_start.hour + block_start.minute / 60 + 7.5 / 60  # block midpoint

            wind_state = min(max(step_wind_state(wind_state, rng), 0.0), 0.95)
            frac = {
                "hydro": hydro_fraction(hour, hydro_day, rng),
                "solar": solar_fraction(hour, solar_day, rng),
                "wind": wind_fraction(wind_state, hour, rng),
            }

            # Generation per station (kW) drives that station's auxiliary load
            station_gen_kw = {
                s.station_id: max(frac[s.source], 0.0) * s.capacity_mw * 1000
                for s in STATIONS
            }

            for m in METERS:
                st = STATION_BY_ID[m.station_id]
                if m.meter_type == "GEN":
                    kw = station_gen_kw[st.station_id] * m.capacity_share
                else:
                    kw = (st.aux_base_kw * rng.uniform(0.9, 1.1)
                          + st.aux_frac * station_gen_kw[st.station_id])

                # The register always advances, even if the read never arrives
                registers[m.meter_id] += kw * 0.25
                row = (m.meter_id, m.station_id, m.meter_type, block_end,
                       round(registers[m.meter_id], 3), round(kw, 3))

                if inject_anomalies:
                    if b in outage_blocks.get((m.meter_id, d), ()):
                        stats["outage_missing"] += 1
                        continue
                    if rng.random() < P_DROP:
                        stats["dropped"] += 1
                        continue
                    if rng.random() < P_DUPLICATE:
                        rows.append(row)
                        stats["duplicated"] += 1

                rows.append(row)

    stats["rows"] = len(rows)
    return rows, stats, registers


# --------------------------------------------------------------------------- #
# Database
# --------------------------------------------------------------------------- #

DDL = """
CREATE SCHEMA IF NOT EXISTS bronze;

CREATE TABLE IF NOT EXISTS bronze.meter_reads (
    read_id       BIGSERIAL     PRIMARY KEY,
    meter_id      TEXT          NOT NULL,
    station_id    TEXT          NOT NULL,
    meter_type    TEXT          NOT NULL,
    reading_ts    TIMESTAMPTZ   NOT NULL,
    kwh_register  NUMERIC(16,3) NOT NULL,
    kw_avg        NUMERIC(12,3) NOT NULL,
    batch_id      UUID          NOT NULL,
    ingested_at   TIMESTAMPTZ   NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_meter_reads_meter_ts
    ON bronze.meter_reads (meter_id, reading_ts);
"""


def db_settings() -> dict:
    # Locally, pick up the repo's .env. In Docker, compose passes the variables.
    try:
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)
    except ImportError:
        pass
    return {
        "host": os.getenv("POSTGRES_HOST", "localhost"),
        "port": int(os.getenv("POSTGRES_PORT", "5432")),
        "user": os.getenv("POSTGRES_USER", "energy"),
        "password": os.getenv("POSTGRES_PASSWORD", "energy_pw"),
        "dbname": os.getenv("POSTGRES_DB", "energy"),
    }


def last_state(conn) -> tuple[datetime | None, dict[str, float]]:
    """Latest reading time and the last register value per meter, if any data exists."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT DISTINCT ON (meter_id) meter_id, kwh_register, reading_ts
            FROM bronze.meter_reads
            ORDER BY meter_id, reading_ts DESC
        """)
        rows = cur.fetchall()
    if not rows:
        return None, {}
    last_ts = max(r[2] for r in rows)
    return last_ts, {r[0]: float(r[1]) for r in rows}


def copy_rows(conn, rows, batch_id: uuid.UUID) -> None:
    sql = ("COPY bronze.meter_reads "
           "(meter_id, station_id, meter_type, reading_ts, kwh_register, kw_avg, batch_id) "
           "FROM STDIN")
    with conn.cursor() as cur, cur.copy(sql) as copy:
        for r in rows:
            copy.write_row((*r, batch_id))


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Generate 15-min energy meter reads into bronze.")
    p.add_argument("--days", type=int, default=30, help="number of days to generate (default 30)")
    p.add_argument("--start", type=date.fromisoformat,
                   help="first day YYYY-MM-DD (default: day after last loaded, "
                        "or --days ago if the table is empty)")
    p.add_argument("--seed", type=int, default=42, help="random seed for reproducible data")
    p.add_argument("--clean", action="store_true", help="no injected anomalies")
    p.add_argument("--reset", action="store_true", help="empty bronze.meter_reads before loading")
    p.add_argument("--dry-run", action="store_true", help="generate and summarise, no database")
    return p.parse_args(argv)


def initial_registers(rng: random.Random) -> dict[str, float]:
    return {m.meter_id: round(rng.uniform(1e7, 5e7), 3) for m in METERS}


def print_summary(start: date, days: int, stats: Counter) -> None:
    end = start + timedelta(days=days - 1)
    print(f"Window       : {start} .. {end} ({days} days, {len(METERS)} meters)")
    print(f"Rows written : {stats['rows']:,}")
    print(f"  dropped    : {stats['dropped']:,}")
    print(f"  outages    : {stats['outage_missing']:,} missing blocks")
    print(f"  duplicates : {stats['duplicated']:,}")


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.days < 1:
        print("--days must be at least 1", file=sys.stderr)
        return 2

    if args.dry_run:
        start = args.start or (date.today() - timedelta(days=args.days))
        rng = random.Random(f"{args.seed}-{start}")
        rows, stats, _ = generate_reads(start, args.days, initial_registers(rng),
                                        rng, not args.clean)
        print_summary(start, args.days, stats)
        print("\nSample rows:")
        for r in rows[:5]:
            print("  ", r)
        return 0

    import psycopg  # imported here so --dry-run works without it

    settings = db_settings()
    print(f"Connecting to {settings['host']}:{settings['port']}/{settings['dbname']} ...")
    with psycopg.connect(**settings) as conn:
        conn.execute(DDL)
        if args.reset:
            conn.execute("TRUNCATE bronze.meter_reads RESTART IDENTITY")
            print("bronze.meter_reads emptied (--reset)")

        last_ts, registers = last_state(conn)
        last_day = last_ts.astimezone(IST).date() if last_ts else None
        # a block ending at 00:00 belongs to the previous day
        if last_ts and last_ts.astimezone(IST).time() == time(0, 0):
            last_day = last_day - timedelta(days=1)

        if args.start:
            start = args.start
            if last_day and start <= last_day:
                print(f"ERROR: bronze already has data up to {last_day}. Pick a --start "
                      f"after that, omit --start to continue, or use --reset.",
                      file=sys.stderr)
                return 1
        else:
            start = (last_day + timedelta(days=1)) if last_day \
                else date.today() - timedelta(days=args.days)

        rng = random.Random(f"{args.seed}-{start}")
        if not registers:
            registers = initial_registers(rng)

        rows, stats, _ = generate_reads(start, args.days, registers, rng, not args.clean)
        batch_id = uuid.uuid4()
        copy_rows(conn, rows, batch_id)
        conn.commit()

        total = conn.execute("SELECT count(*) FROM bronze.meter_reads").fetchone()[0]

    print_summary(start, args.days, stats)
    print(f"Batch id     : {batch_id}")
    print(f"Table total  : {total:,} rows in bronze.meter_reads")
    return 0


if __name__ == "__main__":
    sys.exit(main())
