# Chokepoint

Energy Supply Chain Resilience ingestion pipeline — M0 scaffold, M1 deterministic ingestion, M2 feature extraction + risk scoring. Signals flow end to end: raw pulls → extracted features (LLM for GDELT, rules for OFAC/EIA) → a deterministic corridor risk score. No alerting yet.

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

## Extraction & scoring (M2)

Turns raw signals into `extracted_features`, then a single `risk_scores` row per window:

```bash
python -m src.extraction.run --window-days 1   # needs ANTHROPIC_API_KEY for GDELT only
python -m src.scoring.run --window-days 1
```

- **GDELT**: each pending article gets one tool-forced LLM call validated against the `ExtractedFeature` pydantic schema; invalid output retries exactly once, then the article is skipped (it stays pending for the next run). One bad article never blocks the rest.
- **Provider-agnostic LLM**: `LLM_PROVIDER` selects `groq` (default) | `nvidia` | `mistral` | `openrouter` — all via one OpenAI-compatible httpx client with forced function calling — or `anthropic` (SDK path, `ANTHROPIC_API_KEY`). Set `LLM_API_KEY` + `LLM_MODEL`; `model_used` records what actually ran. Prefer 70B-class models: 8B free variants ignore tool calls more often, and our retry-once absorbs only part of that.
- **OFAC SDN** (deterministic): `event_type=sanctions_listing`, severity by entry type — vessel 4.5 > entity 3.0 > individual 2.0 (unknown 2.5), `confidence=1.0`, `model_used=rule_based`.
- **EIA** (deterministic): `event_type=price_movement`; severity = |day-over-day % change| ÷ trailing-median |% change| (30-point baseline, floored at 1% as a cold-start prior), capped at 5.
- Reruns skip signals that already have an extracted feature — extraction is idempotent.

### Scoring formula (`src/scoring/formula.py`)

```
score = 100 · Σ(wᵢ·cᵢ over sources present in window) / Σ(wᵢ present)
gdelt_component = Σ sev·conf·exp(-ln2·hours/24) / GDELT_NORM(20)     clamp [0,1]
ofac_component  = min(Σ severity, OFAC_CAP(25)) / OFAC_CAP           ← bounded
eia_component   = Σ sev·conf·exp(-ln2·hours/24) / EIA_NORM(10)       clamp [0,1]
weights: W_GDELT=0.5, W_OFAC=0.3, W_EIA=0.2
```

Missing sources renormalize rather than zero-fill. The OFAC cap is deliberate: thousands of accumulated Iran listings must not dominate the score linearly. Known future improvement (not built): OFAC delta/diff detection so new designations spike the score while the standing list contributes a stable baseline. An empty window writes no `risk_scores` row.

Manual end-to-end check:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
python -m src.extraction.run --window-days 1
python -m src.scoring.run --window-days 1
docker compose exec db psql -U postgres -d chokepoint \
  -c "SELECT event_type, model_used, count(*) FROM extracted_features GROUP BY 1,2;" \
  -c "SELECT * FROM risk_scores ORDER BY computed_at DESC LIMIT 1;"
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
