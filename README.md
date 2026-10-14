# Energy Data Platform

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
- [ ] Step 3 — dbt model bronze -> gold (+ test)
- [ ] Step 4 — chart from gold

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
