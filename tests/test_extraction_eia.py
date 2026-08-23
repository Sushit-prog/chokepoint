import pytest
from sqlalchemy import select

from src.db.models import ExtractedFeature as ExtractedFeatureRow
from src.db.session import SessionLocal
from src.extraction.eia_extractor import compute_eia_features, run_pending


def _series(*prices):
    return [
        {"date": f"2026-08-{10 + i:02d}", "price": price}
        for i, price in enumerate(prices)
    ]


def test_first_point_and_empty_input():
    assert compute_eia_features([]) == {}
    features = compute_eia_features(_series(70.0))
    assert features["2026-08-10"].severity == 0.0


def test_flat_prices_produce_zero_severity():
    features = compute_eia_features(_series(70.0, 70.0, 70.0, 70.0))
    assert all(f.severity == 0.0 for f in features.values())


def test_steady_moves_score_baseline_spike_scores_cap():
    steady = compute_eia_features(_series(100.0, 101.0, 102.01, 103.03))
    steady_sevs = [f.severity for _, f in sorted(steady.items())][1:]
    for sev in steady_sevs:
        assert sev == pytest.approx(1.0, abs=1e-3)

    with_spike = compute_eia_features(_series(100.0, 101.0, 102.01, 103.03, 114.47))
    assert with_spike["2026-08-14"].severity == pytest.approx(5.0)


def test_severity_is_capped_at_five():
    features = compute_eia_features(_series(100.0, 200.0))
    assert features["2026-08-11"].severity == 5.0


def test_baseline_is_trailing_not_inclusive_of_current_move():
    features = compute_eia_features(_series(100.0, 101.0, 200.0))
    assert features["2026-08-11"].severity == pytest.approx(1.0)
    assert features["2026-08-12"].severity == pytest.approx(min(5.0, 98.7))


def test_deterministic_same_input_same_output():
    series = _series(64.0, 63.2, 65.9, 65.1, 68.0)
    assert compute_eia_features(series) == compute_eia_features(list(reversed(series)))


def test_run_pending_writes_rows_for_window_signals(sample_window, make_signal):
    make_signal(
        "eia_price",
        {"date": "2026-08-22", "price": 63.5, "series_id": "RWTC"},
    )
    make_signal(
        "eia_price",
        {"date": "2026-08-23", "price": 66.9, "series_id": "RWTC"},
    )

    processed, failed = run_pending(sample_window)

    assert (processed, failed) == (2, 0)
    with SessionLocal() as session:
        rows = session.execute(select(ExtractedFeatureRow)).scalars().all()
    assert len(rows) == 2
    assert all(row.model_used == "rule_based" for row in rows)
    assert all(row.event_type == "price_movement" for row in rows)

    severities = {row.severity for row in rows}
    assert 66.9 - 63.5 > 0
    assert any(sev > 0 for sev in severities)


def test_run_pending_rerun_skips_processed(sample_window, make_signal):
    make_signal(
        "eia_price",
        {"date": "2026-08-23", "price": 63.5, "series_id": "RWTC"},
    )
    run_pending(sample_window)
    processed, failed = run_pending(sample_window)
    assert (processed, failed) == (0, 0)
