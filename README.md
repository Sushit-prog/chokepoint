# Chokepoint

Energy Supply Chain Resilience ingestion pipeline — milestone M0 (scaffold + DB schema + migrations). The app boots, runs migrations, and serves a `/health` endpoint backed by a live Postgres check. No ingestion, AI, scoring, or auth logic yet.

## Stack

- Python 3.11 / FastAPI / Uvicorn
- SQLAlchemy 2.x (psycopg 3) / Alembic migrations
- PostgreSQL 16 via Docker Compose
- pytest

## Prerequisites

- Docker Desktop (with Compose v2)

## Run locally

```bash
cp .env.example .env        # optional; compose has sane defaults
docker compose up -d --build
curl localhost:8000/health
```

Expected response:

```json
{"status": "ok", "db": "connected"}
```

The app container applies `alembic upgrade head` automatically before starting Uvicorn.

> The compose Postgres is published on host port **15432** (not 5432) so it can coexist with a locally installed Postgres or other Docker projects. Override via `POSTGRES_PORT` in `.env`.

## Run tests

Tests require the compose Postgres running (`docker compose up -d db`), then:

```bash
pip install -e ".[dev]"
pytest tests/ -v
```

- `tests/test_health.py` — hits `GET /health`, asserts `200` and `db: connected`
- `tests/test_schema.py` — asserts the unique constraint on `ingestion_jobs(source, window_start, window_end)` and that all FK relationships resolve, against the live database

## Migrations

```bash
alembic upgrade head          # apply all migrations
alembic downgrade -1          # roll back the latest migration
alembic revision --autogenerate -m "..."   # generate a new migration from models
```

Alembic reads the connection string from the `DATABASE_URL` env var, falling back to the value in `alembic.ini`.

## Project structure

```
├── docker-compose.yml       # app + postgres services
├── Dockerfile               # python:3.11-slim; runs migrations then uvicorn
├── pyproject.toml           # dependencies + dev extras
├── alembic/                 # env.py, script templates, versions/
│   └── versions/0001_initial_schema.py
├── src/
│   ├── app.py               # FastAPI app, GET /health
│   └── db/
│       ├── session.py       # engine/session factory
│       └── models.py        # signals, extracted_features, risk_scores, alerts,
│                            # reference_routes, recommendations, ingestion_jobs
├── tests/                   # pytest suite
└── .github/workflows/ci.yml # migrations + tests against a PG service container
```

## Schema overview

| Table | Notes |
|---|---|
| `signals` | raw ingested payloads (JSONB), corridor/commodity tags |
| `extracted_features` | LLM-extracted events, FK → `signals.id` |
| `risk_scores` | per corridor+commodity score, `contributing_signal_ids BIGINT[]` |
| `alerts` | threshold crossings, FK → `risk_scores.id` |
| `reference_routes` | supplier route baselines (lead time, price index) |
| `recommendations` | ranked reroute suggestions, FK → `alerts.id` + `reference_routes.id` |
| `ingestion_jobs` | job bookkeeping, **UNIQUE** on `(source, window_start, window_end)` |

## CI

`.github/workflows/ci.yml` spins up a `postgres:16-alpine` service container, installs deps, runs `alembic upgrade head`, then `pytest`.
