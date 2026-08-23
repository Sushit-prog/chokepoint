import httpx
import pytest
from sqlalchemy import select

from src.db.models import IngestionJob, Signal
from src.db.session import SessionLocal
from src.ingestion.common import resolve_window
from src.ingestion.eia import SERIES_ID, build_params, ingest


SAMPLE_RESPONSE = {
    "response": {
        "total": 2,
        "data": [
            {"period": "2026-08-22", "value": 63.5, "seriesId": "RWTC"},
            {"period": "2026-08-23", "value": 64.25, "seriesId": "RWTC"},
        ],
    }
}


@pytest.fixture
def _eia_api_key(monkeypatch):
    monkeypatch.setenv("EIA_API_KEY", "test-key-123")


def test_build_params_targets_wti_series(sample_window):
    params = build_params(sample_window, "k")
    assert params["facets[series][]"] == SERIES_ID == "RWTC"
    assert params["frequency"] == "daily"
    assert params["data[0]"] == "value"
    assert params["start"] == "2026-08-23"
    assert params["end"] == "2026-08-23"


def test_eia_ingest_stores_daily_price_points(sample_window, _eia_api_key):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, json=SAMPLE_RESPONSE)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    outcome = ingest(sample_window, client=client)

    assert outcome.status == "completed"
    assert outcome.row_count == 2
    assert captured["params"]["api_key"] == "test-key-123"
    assert captured["params"]["facets[series][]"] == "RWTC"

    with SessionLocal() as session:
        rows = (
            session.execute(select(Signal).where(Signal.source == "eia_price"))
            .scalars()
            .all()
        )
    assert len(rows) == 2
    by_date = {row.raw_payload["date"]: row.raw_payload for row in rows}
    assert by_date["2026-08-22"] == {
        "date": "2026-08-22",
        "price": 63.5,
        "series_id": "RWTC",
    }
    assert by_date["2026-08-23"] == {
        "date": "2026-08-23",
        "price": 64.25,
        "series_id": "RWTC",
    }


def test_eia_ingest_fails_fast_without_api_key(sample_window, monkeypatch):
    monkeypatch.delenv("EIA_API_KEY", raising=False)
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=SAMPLE_RESPONSE)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    outcome = ingest(sample_window, client=client)

    assert outcome.status == "failed"
    assert "EIA_API_KEY" in outcome.error
    assert calls == []

    with SessionLocal() as session:
        job = session.execute(select(IngestionJob)).scalars().one()
    assert job.status == "failed"
    assert "EIA_API_KEY" in job.error


def test_resolve_window_buckets_to_utc_midnight():
    from datetime import datetime, timezone

    now = datetime(2026, 8, 24, 15, 30, 45, tzinfo=timezone.utc)
    start, end = resolve_window(now, 1)
    assert end == datetime(2026, 8, 24, tzinfo=timezone.utc)
    assert start == datetime(2026, 8, 23, tzinfo=timezone.utc)


def test_resolve_window_explicit_overrides():
    from datetime import datetime, timezone

    start = datetime(2026, 8, 1, tzinfo=timezone.utc)
    end = datetime(2026, 8, 10, tzinfo=timezone.utc)
    resolved_start, resolved_end = resolve_window(
        None, 1, window_start=start, window_end=end
    )
    assert (resolved_start, resolved_end) == (start, end)


def test_resolve_window_rejects_mismatched_overrides():
    import pytest as _pytest

    from datetime import datetime, timezone

    with _pytest.raises(ValueError):
        resolve_window(datetime.now(timezone.utc), 1, window_start=datetime(2026, 8, 1))
