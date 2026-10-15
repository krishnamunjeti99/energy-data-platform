# Energy Data Platform

[![CI](https://github.com/krishnamunjeti99/energy-data-platform/actions/workflows/ci.yml/badge.svg)](https://github.com/krishnamunjeti99/energy-data-platform/actions/workflows/ci.yml)

An end-to-end energy-meter data platform: a generator produces 15-minute meter
reads, they land in a Postgres **bronze** layer, dbt transforms them into a
**gold** layer, and a chart (later, a BI dashboard and an analyst agent) reads
from gold.

Built as a portfolio project covering Data Engineering, Data Analysis, and
DevOps.

## Architecture (target)

```
generator -> ingestion -> bronze -> silver -> gold -> BI dashboard + analyst agent
                                   (dbt + orchestration + DQ tests)
```

We build it as a thin vertical slice first, then grow outward.

## Build progress

- [x] Step 1 — Postgres running in Docker
- [x] Step 2 — data generator -> bronze
- [x] Step 3 — dbt: bronze -> silver -> gold, with tests
- [x] Step 4 — dashboard on the gold layer
- [x] Step 5 — CI: lint, unit tests, end-to-end pipeline test on every PR
- [x] Step 6 — Airflow: the pipeline runs itself every night

---

## Step 1 — run Postgres

Prerequisites: Docker Desktop (or Docker Engine + Compose).

```bash
# 1. create your env file from the template
cp .env.example .env

# 2. start Postgres in the background
docker compose up -d

# 3. check it is healthy (STATUS should say "healthy")
docker compose ps

# 4. connect and confirm
docker compose exec postgres psql -U energy -d energy -c "select version();"
```

If port 5432 is already taken on your machine (e.g. a local Postgres), set
`POSTGRES_PORT=5433` in `.env`. Tools on your laptop then connect to
`localhost:5433`; containers keep talking to each other on 5432.

You should see the Postgres version string. That's it for Step 1 — the
foundation is up.

To stop: `docker compose down` (keeps data).
To wipe data and start clean: `docker compose down -v`.

---

## Step 2 — generate meter reads into bronze

`generator/generate_meter_reads.py` simulates 15-minute interval reads from
7 meters at three fictional stations and bulk-loads them into
`bronze.meter_reads` with Postgres `COPY`.

| Station | Source | Capacity | Meters |
|---|---|---|---|
| STN-HYD-01 Riverbend Hydro | hydro (peaking) | 120 MW | 2 × GEN, 1 × AUX |
| STN-SOL-01 Sunfield Solar | solar | 50 MW | 1 × GEN, 1 × AUX |
| STN-WND-01 Windridge Wind | wind | 75 MW | 1 × GEN, 1 × AUX |

Each read carries a **cumulative energy register** (`kwh_register`, only ever
increases), the block's average power (`kw_avg`), and `reading_ts` = the end
of the 15-minute block. Interval energy is derived later from register deltas.

Bronze is raw and append-only, and the generator deliberately injects real-world
problems for the later layers to handle: dropped reads, multi-block comms
outages, and duplicate reads.

```bash
# build the image and load 30 days
docker compose run --rm generator --days 30

# run again with no --start: continues from the last loaded day (incremental)
docker compose run --rm generator --days 1

# other options
docker compose run --rm generator --help
docker compose run --rm generator --reset --days 30   # wipe bronze and reload
docker compose run --rm generator --clean --days 30   # no injected anomalies
docker compose run --rm generator --catch-up          # load missing days up to yesterday; no-op if current
```

Check what landed:

```sql
-- reads per meter (96 per day, minus missing, plus duplicates)
select meter_id, count(*) as reads, min(reading_ts), max(reading_ts)
from bronze.meter_reads group by 1 order by 1;

-- duplicates that silver will need to remove
select meter_id, reading_ts, count(*)
from bronze.meter_reads group by 1, 2 having count(*) > 1;
```

---

## Step 3 — transform with dbt (bronze -> silver -> gold)

The `dbt/` project turns raw reads into analysis-ready tables:

| Layer | Model | What it does |
|---|---|---|
| reference | `stations`, `meters` (seeds) | Station and meter master data from CSV |
| silver | `stg_meter_reads` (view) | Removes duplicate reads: one row per meter per block |
| silver | `silver_interval_energy` | Energy per interval from register deltas (`lag()`), flags gap-fills |
| gold | `gold_daily_generation` | Daily generation, auxiliary %, capacity factor, data completeness per station |

**Why register deltas?** Meters report a cumulative total, not per-block energy.
Interval energy is `register - previous register`. A missing read loses no
energy: the next read that arrives covers the gap (`blocks_covered > 1`), and
the row is flagged `is_gap_fill` instead of being dropped or guessed.

**Tests** (run with every build):
- `unique` / `not_null` keys, `accepted_values` on meter type, `relationships` to the seeds
- `assert_interval_energy_not_negative`: a cumulative register can never go backwards
- `assert_one_row_per_station_per_day`: the grain of the gold table
- `assert_aux_pct_within_bounds`: business rule, auxiliary use above 10% means bad metering
- `warn_low_completeness`: warning (not failure) when a station sends < 95% of expected reads
- source freshness on `bronze.meter_reads`

```bash
# seeds + models + tests, in dependency order
docker compose run --rm dbt build

# check that bronze data is recent
docker compose run --rm dbt source freshness

# browse docs and the lineage graph at http://localhost:8080
docker compose run --rm dbt docs generate
docker compose run --rm --service-ports dbt docs serve --host 0.0.0.0 --port 8080
```

Query the result:

```sql
select reading_date, station_name, generation_kwh, aux_pct,
       capacity_factor_pct, completeness_pct
from gold.gold_daily_generation
order by reading_date desc, station_name
limit 9;
```

dbt runs in its own container as your WSL user (`HOST_UID`, default 1000). If
`id -u` prints something other than 1000, add `HOST_UID=<that number>` and
`HOST_GID=<id -g>` to `.env`.

---

## Step 4 — dashboard on the gold layer

`dashboard/app.py` is a Streamlit app that reads only from
`gold.gold_daily_generation`. All business logic lives in dbt; the dashboard
just presents it.

- **Headline numbers:** generation, net export, auxiliary consumption and data
  completeness for the selected range. Ratios are weighted by energy, not
  averaged per day, so a low-output day doesn't count as much as a full one.
- **Daily generation** and **auxiliary consumption %** per station, as two
  separate charts (one measure per chart, no dual axes), with a hover crosshair
  listing every station's value for the day.
- **Daily detail** table with capacity factor, completeness and gap fills.
- Filters for date range and stations. Each station keeps its colour when
  others are filtered out; the palette is colour-blind validated, with light
  and dark variants.

```bash
docker compose up -d dashboard
# open http://localhost:8501
```

If port 8501 is taken, set `DASHBOARD_PORT=8502` (or any free port) in `.env`.

After loading new data, refresh the whole pipeline with:

```bash
docker compose run --rm generator --days 1   # next day of reads into bronze
docker compose run --rm dbt build            # rebuild silver + gold, run tests
# the dashboard picks up the new gold data within a minute
```

---

## Step 5 — continuous integration (GitHub Actions)

`.github/workflows/ci.yml` runs on every pull request and every push to `main`:

| Job | What it proves |
|---|---|
| **Lint + unit tests** | `ruff` passes; generator logic is correct (every block present in clean mode, registers never go backwards, anomalies are injected, same seed gives same data, solar is dark at night) |
| **End-to-end pipeline** | Against a fresh Postgres: generate 30 days, run an incremental day, `dbt build` (all models + 20 tests), source freshness, and a smoke test that the dashboard renders from gold |
| **Docker images build** | Every Dockerfile still builds |

Run the same checks locally before pushing:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
ruff check .
pytest            # database tests skip unless POSTGRES_HOST is set
```

---

## Step 6 — orchestration with Airflow

`airflow/dags/energy_daily_pipeline.py` runs the whole pipeline every night at
01:30 India time:

```
load_meter_reads  ->  dbt_source_freshness  ->  dbt_build  ->  check_gold
```

| Task | What it does |
|---|---|
| `load_meter_reads` | `generator --catch-up`: loads every missing day up to yesterday; a no-op if bronze is current, so reruns never duplicate data, and a pipeline that was down for 3 days catches up on its own |
| `dbt_source_freshness` | fails fast if bronze didn't actually receive new data |
| `dbt_build` | rebuilds silver and gold and runs all dbt tests; a failing test fails the run, so bad data never silently reaches the dashboard |
| `check_gold` | confirms yesterday is in gold (via an Airflow connection + `PostgresHook`) and logs completeness per station |

Design choices:
- **Retries** (2, two minutes apart) for transient failures like a database restart.
- **`catchup=False` + an idempotent catch-up loader** instead of one Airflow run per missed day.
- **`max_active_runs=1`** so two runs never write to bronze at the same time.
- **Isolated virtualenv** for dbt and the generator inside the Airflow image, so their dependencies never clash with Airflow's.
- **Standalone mode** (scheduler, UI, DAG processor and triggerer in one container, ~1.5 GB) with Airflow's metadata in the existing Postgres, to fit a laptop. The DAG code is identical to a production deployment.

```bash
docker compose up -d airflow     # first start takes ~1 minute
# open http://localhost:8090, unpause energy_daily_pipeline, click Trigger
```

If port 8090 is taken, set `AIRFLOW_PORT=8091` (or any free port) in `.env`.
