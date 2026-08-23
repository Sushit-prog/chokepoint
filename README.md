# Chokepoint

Energy Supply Chain Resilience ingestion pipeline — M0 scaffold + M1 deterministic ingestion. The app boots, runs migrations, serves a `/health` endpoint backed by a live Postgres check, and ingests GDELT news, OFAC SDN, and EIA crude spot prices into `signals` idempotently. No LLM extraction, scoring, or auth yet.

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
- `tests/test_ingestion_*.py` — source parsers + orchestrator, fully mocked HTTP (`httpx.MockTransport`), no real API calls in CI

## Ingestion (M1)

Pulls raw signals for corridor `hormuz`, commodity `crude_oil` from three sources:

| Source | Auth | What lands in `signals.raw_payload` |
|---|---|---|
| `gdelt` | none | GDELT DOC 2.0 artlist articles matching `"Strait of Hormuz" OR "Iran oil sanctions" OR "Red Sea shipping crude"` |
| `ofac_sdn` | none | SDN CSV rows whose Program field contains `IRAN` (named-field JSON, `-0-` nulls normalized) |
| `eia_price` | `EIA_API_KEY` | daily WTI Cushing spot (series `RWTC`) points: `{date, price, series_id}` |

```bash
python -m src.ingestion.run --window-days 1
```

- The window is **UTC-day bucketed**: it ends at the most recent UTC midnight and spans N days back, so rerunning the same day reuses the identical `(source, window_start, window_end)` job tuple.
- Each source gets an `ingestion_jobs` row; a job already marked `completed` is **skipped** — that is what makes immediate reruns store zero new rows. Failed jobs record the error and are retried on the next run.
- Sources are isolated: one failing never blocks the others. Per-source status lines are printed and the CLI exits non-zero if anything failed.
- Optional explicit windows for backfills: `--window-start "2026-08-01T00:00:00+00:00" --window-end "2026-08-03T00:00:00+00:00"`.

Manual end-to-end check against real APIs (not run in CI):

```bash
cp .env.example .env          # set EIA_API_KEY (free: https://www.eia.gov/opendata/register.php)
docker compose up -d db
python -m src.ingestion.run --window-days 1
python -m src.ingestion.run --window-days 1   # expect all [skipped]
docker compose exec db psql -U postgres -d chokepoint \
  -c "SELECT source, count(*) FROM signals GROUP BY source;"
```

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
│   ├── config.py            # env-driven settings (EIA_API_KEY, URLs, timeouts)
│   └── db/
│       ├── session.py       # engine/session factory
│       └── models.py        # signals, extracted_features, risk_scores, alerts,
│                            # reference_routes, recommendations, ingestion_jobs
│   └── ingestion/
│       ├── common.py        # window resolution, retry HTTP, job lifecycle
│       ├── gdelt.py         # GDELT DOC 2.0 client + parser
│       ├── ofac.py          # OFAC SDN CSV client + parser
│       ├── eia.py           # EIA v2 WTI spot price client + parser
│       └── run.py           # CLI: python -m src.ingestion.run --window-days N
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
