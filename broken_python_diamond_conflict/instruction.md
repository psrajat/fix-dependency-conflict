# Task: Restore the Event Ingestion Service

## Background

You are inheriting a small Python event-ingestion service in `/app/`.

- The application entry point is `/app/app.py`.
- The application install is rooted in dependency-related files under `/app/`.
- Vendored internal packages live under `/app/packages/`.

The service is supposed to accept events, process them through its existing SDK
integration, and expose a reporting summary.

A recent change broke the application in a way that is not immediately visible
from startup alone: the service still starts, but one key request path does not
work correctly.

## What Should Work

1. `cd /app && python app.py` starts the HTTP service on port 8000.
2. `GET /health` returns HTTP 200 with `{"status": "ok"}`.
3. `POST /ingest` with a valid event returns HTTP 202.
4. `GET /reports/summary` returns aggregated counts for previously ingested events.

## Environment

- The application code is in `/app/app.py`.
- Dependency-related files live under `/app/`.
- Internal vendored packages are under `/app/packages/`.

## Rules

- All fixes must be applied inside `/app/`.
- Fix the root cause. Do not bypass the SDKs or inline their logic into the
  application.
- Keep the HTTP API shape the same.
- Do not rewrite the vendored SDK implementation files from scratch.
- A correct fix should survive a fresh reinstall from the files in `/app/`.

