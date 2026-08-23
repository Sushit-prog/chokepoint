import pytest
from sqlalchemy import select

from src.db.models import ExtractedFeature as ExtractedFeatureRow
from src.db.session import SessionLocal
from src.extraction import run as extraction_run
from src.extraction.gdelt_extractor import run_pending as run_gdelt_pending
from tests.test_extraction_gdelt import StubClient, _tool_response


def test_run_all_isolates_source_failures():
    def ok_runner(window):
        return 3, 0

    def partial_runner(window):
        return 1, 2

    def broken_runner(window):
        raise RuntimeError("LLM provider down")

    sources = (
        ("gdelt", broken_runner),
        ("ofac_sdn", ok_runner),
        ("eia_price", partial_runner),
    )
    results = extraction_run.run_all(sample_window_tuple(), sources=sources)

    assert results == [
        ("gdelt", 0, 0, "LLM provider down"),
        ("ofac_sdn", 3, 0, None),
        ("eia_price", 1, 2, None),
    ]


def sample_window_tuple():
    from datetime import datetime, timezone

    return (
        datetime(2026, 8, 23, tzinfo=timezone.utc),
        datetime(2026, 8, 24, tzinfo=timezone.utc),
    )


def test_main_exit_zero_when_all_clean(monkeypatch, capsys):
    monkeypatch.setattr(
        extraction_run,
        "DEFAULT_SOURCES",
        (
            ("gdelt", lambda w: (2, 0)),
            ("ofac_sdn", lambda w: (0, 0)),
            ("eia_price", lambda w: (1, 0)),
        ),
    )
    code = extraction_run.main(["--window-days", "1"])
    out = capsys.readouterr().out
    assert code == 0
    assert "[gdelt] processed:2 failed:0" in out
    assert "[ofac_sdn] processed:0 failed:0" in out
    assert "[eia_price] processed:1 failed:0" in out


def test_main_exit_one_on_per_article_failures(monkeypatch, capsys):
    monkeypatch.setattr(
        extraction_run,
        "DEFAULT_SOURCES",
        (("gdelt", lambda w: (5, 3)),),
    )
    code = extraction_run.main(["--window-days", "1"])
    out = capsys.readouterr().out
    assert code == 1
    assert "[gdelt] processed:5 failed:3" in out


def test_main_exit_one_on_source_crash(monkeypatch, capsys):
    def broken(window):
        raise ValueError("no api key")

    monkeypatch.setattr(extraction_run, "DEFAULT_SOURCES", (("gdelt", broken),))
    code = extraction_run.main(["--window-days", "1"])
    out = capsys.readouterr().out
    assert code == 1
    assert "[gdelt] source failed: no api key" in out


def test_main_honors_explicit_window(monkeypatch):
    recorded = {}

    def spy(window):
        recorded["window"] = window
        return 0, 0

    monkeypatch.setattr(extraction_run, "DEFAULT_SOURCES", (("gdelt", spy),))
    extraction_run.main(
        [
            "--window-start",
            "2026-08-01T00:00:00+00:00",
            "--window-end",
            "2026-08-05T00:00:00+00:00",
        ]
    )

    from datetime import datetime, timezone

    assert recorded["window"] == (
        datetime(2026, 8, 1, tzinfo=timezone.utc),
        datetime(2026, 8, 5, tzinfo=timezone.utc),
    )


def test_main_rejects_invalid_args():
    with pytest.raises(SystemExit):
        extraction_run.main(["--window-start", "2026-08-01T00:00:00+00:00"])
    with pytest.raises(SystemExit):
        extraction_run.main(["--window-days", "-2"])


def test_extraction_rerun_skips_processed_signals_end_to_end(
    sample_window, make_signal
):
    signal_id = make_signal(
        "gdelt",
        {"url": "https://example.com/e2e", "title": "End to end rerun"},
    )

    first = StubClient(
        [
            _tool_response(
                {
                    "event_type": "geopolitical_tension",
                    "severity": 3.0,
                    "confidence": 0.8,
                }
            )
        ]
    )
    assert run_gdelt_pending(sample_window, client=first) == (1, 0)

    second = StubClient([])
    assert run_gdelt_pending(sample_window, client=second) == (0, 0)
    assert second.messages.calls == []

    with SessionLocal() as session:
        rows = session.execute(select(ExtractedFeatureRow)).scalars().all()
    assert len(rows) == 1 and rows[0].signal_id == signal_id
