FROM python:3.12-slim@sha256:cb33f7f771ecec2ef1225cf39b159591a040391bd73e4c01f00fdfc4c47611d2
WORKDIR /srv/service
COPY requirements.txt requirements.lock requirements.linux.lock ./
RUN pip install --no-cache-dir -c requirements.linux.lock -r requirements.txt
COPY app ./app
COPY migrations ./migrations
COPY alembic.ini ./
COPY scripts/worker_healthcheck.py scripts/check_release_inputs.py scripts/check_database.py scripts/check_schema_baseline.py scripts/migrate_history.py scripts/reconcile_audio.py scripts/normalize_audio_keys.py scripts/verify_recovery_manifest.py scripts/migrate_local.py ./scripts/
RUN useradd --system --uid 10001 service && mkdir -p /srv/service/var/audio && chown -R service:service /srv/service
USER service
EXPOSE 8100
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8100"]
