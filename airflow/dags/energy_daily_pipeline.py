"""
### Energy daily pipeline

Runs every night at 01:30 India time, after the previous day's last meter reads
have arrived:

1. **load_meter_reads**: load every missing day of reads into bronze, up to
   yesterday. A no-op when bronze is already current, so reruns are safe.
2. **dbt_source_freshness**: fail fast if bronze didn't actually receive data.
3. **dbt_build**: rebuild silver and gold and run all dbt tests.
4. **check_gold**: confirm yesterday's numbers landed in gold and log a
   data-quality summary per station.

Retries handle transient failures (a database restart, a network blip). A failed
dbt test also fails the run, so bad data never silently reaches the dashboard.
"""

import os
from datetime import timedelta

import pendulum
from airflow.providers.postgres.hooks.postgres import PostgresHook
from airflow.providers.standard.operators.bash import BashOperator
from airflow.sdk import dag, task

# Paths inside the Airflow container (overridable for local testing)
PROJECT_DIR = os.getenv("PROJECT_DIR", "/opt/project")
PIPELINE_BIN = os.getenv("PIPELINE_BIN", "/opt/pipeline-venv/bin")
IST = "Asia/Kolkata"

default_args = {
    "owner": "data-eng",
    "retries": 2,
    "retry_delay": timedelta(minutes=2),
}


@dag(
    dag_id="energy_daily_pipeline",
    description="Meter reads -> bronze -> dbt (silver, gold, tests) -> gold check",
    schedule="30 1 * * *",
    start_date=pendulum.datetime(2026, 10, 1, tz=IST),
    catchup=False,          # the loader catches up on its own; no backlog of runs
    max_active_runs=1,      # never two loads writing to bronze at once
    default_args=default_args,
    tags=["energy", "dbt"],
    doc_md=__doc__,
)
def energy_daily_pipeline():

    load_meter_reads = BashOperator(
        task_id="load_meter_reads",
        bash_command=(f"{PIPELINE_BIN}/python "
                      f"{PROJECT_DIR}/generator/generate_meter_reads.py --catch-up"),
    )

    dbt_source_freshness = BashOperator(
        task_id="dbt_source_freshness",
        bash_command=f"{PIPELINE_BIN}/dbt source freshness",
        cwd=f"{PROJECT_DIR}/dbt",
    )

    dbt_build = BashOperator(
        task_id="dbt_build",
        bash_command=f"{PIPELINE_BIN}/dbt build",
        cwd=f"{PROJECT_DIR}/dbt",
    )

    @task(retries=0)
    def check_gold():
        """Fail if yesterday is missing from gold; log completeness per station."""
        day = pendulum.now(IST).subtract(days=1).date()
        hook = PostgresHook(postgres_conn_id="energy_warehouse")
        rows = hook.get_records(
            """
            select station_name, round(generation_kwh / 1000, 1),
                   aux_pct, completeness_pct, gap_fills
            from gold.gold_daily_generation
            where reading_date = %s
            order by station_id
            """,
            parameters=(day,),
        )
        if not rows:
            raise ValueError(f"No gold rows for {day}: the pipeline produced nothing.")

        print(f"Gold summary for {day}:")
        for name, gen_mwh, aux_pct, completeness, gaps in rows:
            flag = "  <-- below 95% completeness" if completeness < 95 else ""
            print(f"  {name:<16} {gen_mwh:>8} MWh  aux {aux_pct}%  "
                  f"complete {completeness}%  gap fills {gaps}{flag}")
        return {"day": str(day), "stations": len(rows)}

    load_meter_reads >> dbt_source_freshness >> dbt_build >> check_gold()


energy_daily_pipeline()
