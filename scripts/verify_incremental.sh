#!/usr/bin/env bash
# Proves the incremental models give exactly the same result as a full rebuild.
#
# 1. dbt build              (incremental: processes only new data)
# 2. copy silver and gold aside
# 3. dbt build --full-refresh (rebuilds everything from bronze)
# 4. compare row by row in both directions; any difference fails
#
# Run it after loading new data, so step 1 actually has something to merge.
#
# CI (dbt and psql installed, PG* variables set):
#     scripts/verify_incremental.sh
# Locally with docker compose:
#     DBT="docker compose run --rm -T dbt" \
#     PSQL="docker compose exec -T postgres psql -U energy -d energy" \
#     scripts/verify_incremental.sh
set -euo pipefail

DBT=${DBT:-dbt}
export DBT_PROJECT_DIR=${DBT_PROJECT_DIR:-dbt}   # used by a locally installed dbt
PSQL=${PSQL:-psql}
sql() { $PSQL -qXtA -v ON_ERROR_STOP=1 -c "set client_min_messages = warning; $1"; }

echo "1/4 incremental build"
$DBT build --quiet

echo "2/4 saving the incremental result"
sql "drop schema if exists verify_incremental cascade;
     create schema verify_incremental;
     create table verify_incremental.silver as select * from silver.silver_interval_energy;
     create table verify_incremental.gold   as select * from gold.gold_daily_generation;"

echo "3/4 full rebuild"
$DBT build --full-refresh --quiet

echo "4/4 comparing"
differences() {
    sql "select count(*) from (
           (select * from $1 except all select * from $2)
           union all
           (select * from $2 except all select * from $1)
         ) d"
}
silver_rows=$(sql "select count(*) from silver.silver_interval_energy")
gold_rows=$(sql "select count(*) from gold.gold_daily_generation")
silver_diff=$(differences verify_incremental.silver silver.silver_interval_energy)
gold_diff=$(differences verify_incremental.gold gold.gold_daily_generation)
sql "drop schema verify_incremental cascade"

echo "   silver: $silver_rows rows, $silver_diff differ"
echo "   gold:   $gold_rows rows, $gold_diff differ"
if [ "$silver_diff" = "0" ] && [ "$gold_diff" = "0" ]; then
    echo "PASS: incremental models match a full rebuild exactly"
else
    echo "FAIL: incremental and full-refresh results differ" >&2
    exit 1
fi
