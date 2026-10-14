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
- [ ] Step 2 — data generator -> bronze
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

You should see the Postgres version string. That's it for Step 1 — the
foundation is up.

To stop: `docker compose down` (keeps data).
To wipe data and start clean: `docker compose down -v`.
