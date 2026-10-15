FROM apache/airflow:3.3.2-python3.12

# The generator and dbt run from their own virtualenv, isolated from Airflow's
# packages. The project code itself is mounted in by docker compose.
USER root
COPY requirements-pipeline.txt /tmp/requirements-pipeline.txt
RUN python -m venv /opt/pipeline-venv \
 && /opt/pipeline-venv/bin/pip install --no-cache-dir -r /tmp/requirements-pipeline.txt \
 && rm /tmp/requirements-pipeline.txt
USER airflow
