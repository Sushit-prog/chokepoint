import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.db.models import IngestionJob, Signal
from src.db.session import SessionLocal

logger = logging.getLogger("chokepoint.ingestion")

SOURCE_GDELT = "gdelt"
SOURCE_OFAC_SDN = "ofac_sdn"
SOURCE_EIA_PRICE = "eia_price"

JOB_STATUS_PENDING = "pending"
JOB_STATUS_RUNNING = "running"
JOB_STATUS_COMPLETED = "completed"
JOB_STATUS_FAILED = "failed"


class SourceFailure(Exception):
    pass


USER_AGENT = "chokepoint-ingestion/0.1 (+https://github.com/Sushit-prog/chokepoint)"


def build_http_client(settings) -> httpx.Client:
    return httpx.Client(
        timeout=settings.http_timeout_seconds,
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT},
    )


@dataclass
class SourceOutcome:
    source: str
    status: str
    row_count: int = 0
    error: str | None = None


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def resolve_window(
    now: datetime,
    window_days: int,
    window_start: datetime | None = None,
    window_end: datetime | None = None,
) -> tuple[datetime, datetime]:
    if window_days < 1:
        raise ValueError("window_days must be >= 1")
    if window_start is not None or window_end is not None:
        if window_start is None or window_end is None:
            raise ValueError("window_start and window_end must be provided together")
        start = _ensure_utc(window_start)
        end = _ensure_utc(window_end)
        if start >= end:
            raise ValueError("window_start must be before window_end")
        return start, end
    end = now.astimezone(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    start = end - timedelta(days=window_days)
    return start, end


def fetch_with_retry(
    client: httpx.Client,
    url: str,
    params: dict | None = None,
    retry_delay_seconds: float = 2.0,
    max_attempts: int = 2,
) -> httpx.Response:
    for attempt in range(max_attempts):
        response = client.get(url, params=params)
        if response.status_code < 400:
            return response
        if response.status_code < 500:
            raise SourceFailure(f"GET {url} failed with HTTP {response.status_code}")
        if attempt + 1 < max_attempts:
            time.sleep(retry_delay_seconds)
    raise SourceFailure(
        f"GET {url} failed with HTTP {response.status_code} after {max_attempts} attempts"
    )


def get_or_create_job(
    session,
    source: str,
    window_start: datetime,
    window_end: datetime,
) -> IngestionJob:
    stmt = (
        pg_insert(IngestionJob)
        .values(
            source=source,
            window_start=window_start,
            window_end=window_end,
            status=JOB_STATUS_PENDING,
        )
        .on_conflict_do_nothing(constraint="uq_ingestion_jobs_source_window")
    )
    session.execute(stmt)
    session.commit()
    job = session.execute(
        select(IngestionJob).filter_by(
            source=source,
            window_start=window_start,
            window_end=window_end,
        )
    ).scalar_one()
    return job


def run_source(
    *,
    source: str,
    window: tuple[datetime, datetime],
    fetch_fn,
    corridor: str,
    commodity: str,
    session_factory=SessionLocal,
) -> SourceOutcome:
    window_start, window_end = window
    with session_factory() as session:
        job = get_or_create_job(session, source, window_start, window_end)
        if job.status == JOB_STATUS_COMPLETED:
            logger.info("[%s] skipped: already completed for this window", source)
            return SourceOutcome(source, "skipped")
        job.status = JOB_STATUS_RUNNING
        session.commit()

    try:
        payloads = fetch_fn(window)
        with session_factory() as session:
            managed_job = session.get(IngestionJob, job.id)
            session.add_all(
                Signal(
                    source=source,
                    raw_payload=payload,
                    corridor=corridor,
                    commodity=commodity,
                )
                for payload in payloads
            )
            managed_job.status = JOB_STATUS_COMPLETED
            managed_job.error = None
            session.commit()
        logger.info("[%s] completed: %d signals stored", source, len(payloads))
        return SourceOutcome(source, JOB_STATUS_COMPLETED, row_count=len(payloads))
    except Exception as exc:
        with session_factory() as session:
            failed_job = session.get(IngestionJob, job.id)
            failed_job.status = JOB_STATUS_FAILED
            failed_job.error = str(exc)[:2000]
            session.commit()
        logger.error("[%s] failed: %s", source, exc)
        return SourceOutcome(source, JOB_STATUS_FAILED, error=str(exc))
