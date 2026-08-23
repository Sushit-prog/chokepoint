import httpx
import pytest
from sqlalchemy import func, select

from src.db.models import IngestionJob, Signal
from src.db.session import SessionLocal
from src.ingestion import run as run_module
from src.ingestion.common import SourceOutcome
from src.ingestion.eia import ingest as ingest_eia
from src.ingestion.gdelt import ingest as ingest_gdelt
from src.ingestion.ofac import ingest as ingest_ofac


GDELT_OK = {"articles": [{"url": "https://example.com/a", "title": "Strait update"}]}
OFAC_OK = '1,"Iran Front Co","entity","IRAN",-0-,-0-,-0-,-0-,-0-,-0-,-0-,-0-\n'
EIA_OK = {
    "response": {
        "total": 1,
        "data": [{"period": "2026-08-23", "value": 62.0, "seriesId": "RWTC"}],
    }
}


@pytest.fixture
def _api_keys(monkeypatch):
    monkeypatch.setenv("EIA_API_KEY", "test-key-123")


def _router_handler(calls, gdelt_response_factory):
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.host)
        host = request.url.host
        if host.endswith("gdeltproject.org"):
            return gdelt_response_factory()
        if host.endswith("treasury.gov"):
            return httpx.Response(200, text=OFAC_OK)
        if host.endswith("eia.gov"):
            return httpx.Response(200, json=EIA_OK)
        raise AssertionError(f"unexpected host {host}")

    return handler


def _make_sources(calls, gdelt_response_factory):
    client = httpx.Client(
        transport=httpx.MockTransport(_router_handler(calls, gdelt_response_factory))
    )
    return [
        lambda w: ingest_gdelt(w, client=client),
        lambda w: ingest_ofac(w, client=client),
        lambda w: ingest_eia(w, client=client),
    ]


def _signal_counts_by_source():
    with SessionLocal() as session:
        rows = session.execute(
            select(Signal.source, func.count()).group_by(Signal.source)
        ).all()
    return {source: count for source, count in rows}


def test_running_same_window_twice_does_not_duplicate_signals(sample_window, _api_keys):
    calls = []
    sources_first = _make_sources(calls, lambda: httpx.Response(200, json=GDELT_OK))

    first = run_module.run_all(sample_window, sources=sources_first)
    assert [outcome.status for outcome in first] == [
        "completed",
        "completed",
        "completed",
    ]
    counts_after_first = _signal_counts_by_source()
    assert counts_after_first == {"gdelt": 1, "ofac_sdn": 1, "eia_price": 1}
    assert len(calls) == 3

    sources_second = _make_sources(calls, lambda: httpx.Response(200, json=GDELT_OK))
    second = run_module.run_all(sample_window, sources=sources_second)
    assert [outcome.status for outcome in second] == ["skipped", "skipped", "skipped"]
    assert _signal_counts_by_source() == counts_after_first
    assert len(calls) == 3


def test_failed_source_does_not_block_the_others(sample_window, _api_keys):
    calls = []
    sources = _make_sources(calls, lambda: httpx.Response(500, text="upstream down"))

    outcomes = run_module.run_all(sample_window, sources=sources)
    statuses = {outcome.source: outcome.status for outcome in outcomes}
    assert statuses == {
        "gdelt": "failed",
        "ofac_sdn": "completed",
        "eia_price": "completed",
    }

    counts = _signal_counts_by_source()
    assert "gdelt" not in counts
    assert counts["ofac_sdn"] == 1
    assert counts["eia_price"] == 1

    with SessionLocal() as session:
        job = (
            session.execute(select(IngestionJob).where(IngestionJob.source == "gdelt"))
            .scalars()
            .one()
        )
    assert job.status == "failed"
    assert job.error

    retried = run_module.run_all(
        sample_window,
        sources=_make_sources(calls, lambda: httpx.Response(200, json=GDELT_OK)),
    )
    statuses_after_retry = {outcome.source: outcome.status for outcome in retried}
    assert statuses_after_retry == {
        "gdelt": "completed",
        "ofac_sdn": "skipped",
        "eia_price": "skipped",
    }
    counts_final = _signal_counts_by_source()
    assert counts_final["gdelt"] == 1


def test_gdelt_retries_once_before_failing(sample_window, _api_keys):
    calls = []
    sources = _make_sources(calls, lambda: httpx.Response(503, text="unavailable"))
    run_module.run_all(sample_window, sources=sources)
    gdelt_calls = [host for host in calls if host.endswith("gdeltproject.org")]
    assert len(gdelt_calls) == 2


def test_cli_reports_each_source_and_exit_codes(capsys, monkeypatch):
    outcomes = [
        SourceOutcome("gdelt", "completed", row_count=5),
        SourceOutcome("ofac_sdn", "skipped"),
        SourceOutcome("eia_price", "failed", error="boom"),
    ]
    recorded = {}

    def fake_run_all(window, sources=None):
        recorded["window"] = window
        return outcomes

    monkeypatch.setattr(run_module, "run_all", fake_run_all)

    exit_code = run_module.main(["--window-days", "3"])
    output = capsys.readouterr().out

    assert exit_code == 1
    assert "[gdelt] completed: 5 signals stored" in output
    assert "[ofac_sdn] skipped" in output
    assert "[eia_price] failed: boom" in output
    assert recorded["window"] is not None


def test_cli_exits_zero_when_no_failures(capsys, monkeypatch):
    outcomes = [
        SourceOutcome("gdelt", "completed", row_count=1),
        SourceOutcome("ofac_sdn", "completed", row_count=1),
        SourceOutcome("eia_price", "completed", row_count=1),
    ]
    monkeypatch.setattr(run_module, "run_all", lambda window, sources=None: outcomes)
    exit_code = run_module.main([])
    assert exit_code == 0


def test_cli_honors_explicit_window_arguments(monkeypatch):
    from datetime import datetime, timezone

    recorded = {}

    def fake_run_all(window, sources=None):
        recorded["window"] = window
        return []

    monkeypatch.setattr(run_module, "run_all", fake_run_all)
    run_module.main(
        [
            "--window-start",
            "2026-08-01T00:00:00+00:00",
            "--window-end",
            "2026-08-03T00:00:00+00:00",
        ]
    )
    assert recorded["window"] == (
        datetime(2026, 8, 1, tzinfo=timezone.utc),
        datetime(2026, 8, 3, tzinfo=timezone.utc),
    )


def test_cli_rejects_invalid_windows():
    with pytest.raises(SystemExit):
        run_module.main(["--window-start", "2026-08-01T00:00:00+00:00"])
    with pytest.raises(SystemExit):
        run_module.main(["--window-days", "0"])
