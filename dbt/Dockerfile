FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1
RUN pip install --no-cache-dir dbt-core==1.12.5 dbt-postgres==1.11.0

# The dbt project itself is mounted here by docker compose (not copied in),
# so edits you make in VS Code take effect without rebuilding the image.
WORKDIR /usr/app
ENTRYPOINT ["dbt"]
