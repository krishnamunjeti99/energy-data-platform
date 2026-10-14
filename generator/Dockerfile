FROM python:3.12-slim

# Don't buffer output, so progress shows up immediately in `docker compose run`
ENV PYTHONUNBUFFERED=1

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY generate_meter_reads.py .

# Run as a non-root user
RUN useradd --create-home appuser
USER appuser

ENTRYPOINT ["python", "generate_meter_reads.py"]
