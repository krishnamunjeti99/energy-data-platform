"""The Airflow DAG imports cleanly and has the expected shape.

Runs in CI's Airflow job; skipped wherever Airflow isn't installed.
"""

from pathlib import Path

import pytest

pytest.importorskip("airflow")

from airflow.dag_processing.dagbag import DagBag  # noqa: E402

DAGS = Path(__file__).resolve().parent.parent / "airflow" / "dags"


@pytest.fixture(scope="module")
def dagbag():
    return DagBag(dag_folder=str(DAGS))


def test_no_import_errors(dagbag):
    assert dagbag.import_errors == {}


def test_pipeline_shape(dagbag):
    dag = dagbag.get_dag("energy_daily_pipeline")
    assert dag is not None
    assert not dag.catchup
    assert dag.max_active_runs == 1

    order = ["load_meter_reads", "dbt_source_freshness", "dbt_build", "check_gold"]
    assert sorted(dag.task_ids) == sorted(order)
    for upstream, downstream in zip(order, order[1:], strict=False):
        assert downstream in dag.get_task(upstream).downstream_task_ids
