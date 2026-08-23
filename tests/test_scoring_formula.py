from datetime import datetime, timedelta, timezone

import pytest

from src.scoring.formula import (
    DECAY_HALF_LIFE_HOURS,
    EIA_NORM,
    GDELT_NORM,
    OFAC_CAP,
    W_EIA,
    W_GDELT,
    W_OFAC,
    FeatureInput,
    compute_score,
    decay_factor,
)

NOW = datetime(2026, 8, 24, tzinfo=timezone.utc)


def feat(signal_id, source, severity, confidence=1.0, hours_ago=0.0):
    return FeatureInput(
        signal_id=signal_id,
        source=source,
        severity=severity,
        confidence=confidence,
        timestamp=NOW - timedelta(hours=hours_ago),
    )


def test_weights_are_named_constants_summing_to_one():
    assert W_GDELT + W_OFAC + W_EIA == pytest.approx(1.0)
    assert OFAC_CAP == 25.0 and GDELT_NORM == 20.0 and EIA_NORM == 10.0
    assert DECAY_HALF_LIFE_HOURS == 24.0


def test_empty_window_returns_none_score():
    result = compute_score([], NOW)
    assert result.score is None
    assert result.contributing_signal_ids == []


def test_decay_factor_halves_at_half_life():
    assert decay_factor(0.0) == 1.0
    assert decay_factor(DECAY_HALF_LIFE_HOURS) == pytest.approx(0.5)
    assert decay_factor(-5.0) == 1.0


def test_gdelt_only_high_severity_saturates():
    features = [feat(i, "gdelt", 5.0, 1.0, hours_ago=i * 0.1) for i in range(6)]
    result = compute_score(features, NOW)
    assert result.score == pytest.approx(100.0)


def test_gdelt_only_low_severity_scales_linearly_below_norm():
    features = [feat(1, "gdelt", 2.0)]  # sum 2 -> component 0.1
    result = compute_score(features, NOW)
    assert result.score == pytest.approx(100.0 * 0.1)


def test_ofac_contribution_capped_regardless_of_row_count():
    massive = [feat(i, "ofac_sdn", 3.0) for i in range(2500)]
    large = [feat(i, "ofac_sdn", 3.0) for i in range(200)]
    r_massive = compute_score(massive, NOW)
    r_large = compute_score(large, NOW)

    assert r_massive.ofac_component == pytest.approx(1.0)
    assert r_large.ofac_component == pytest.approx(1.0)
    assert r_massive.score == pytest.approx(r_large.score)
    assert r_massive.contributing_signal_ids != r_large.contributing_signal_ids


def test_ofac_scales_with_total_not_count_below_cap():
    many_small = compute_score([feat(i, "ofac_sdn", 0.01) for i in range(2000)], NOW)
    few_larger = compute_score([feat(i, "ofac_sdn", 0.05) for i in range(400)], NOW)

    assert many_small.ofac_component == pytest.approx(20.0 / OFAC_CAP)
    assert few_larger.ofac_component == pytest.approx(20.0 / OFAC_CAP)
    assert many_small.score == pytest.approx(few_larger.score)


def test_ofac_only_sub_cap_scores_proportionally():
    result = compute_score([feat(i, "ofac_sdn", 3.0) for i in range(5)], NOW)
    assert result.ofac_component == pytest.approx(15.0 / OFAC_CAP)
    assert result.score == pytest.approx(100.0 * 0.6)


def test_eia_volatility_spike_contributes():
    calm = compute_score([feat(1, "eia_price", 0.1)], NOW)
    spike = compute_score([feat(1, "eia_price", 5.0)], NOW)
    assert calm.score == pytest.approx(100.0 * 0.1 / EIA_NORM)
    assert spike.score == pytest.approx(100.0 * (5.0 / EIA_NORM))
    assert spike.score > calm.score


def test_mixed_sources_hand_computed_expected_value():
    features = (
        [feat(1, "gdelt", 2.0, 1.0)]
        + [feat(i, "ofac_sdn", 3.0) for i in range(2, 7)]
        + [feat(7, "eia_price", 2.5, 0.8)]
    )
    result = compute_score(features, NOW)

    expected_gdelt = 2.0 / GDELT_NORM
    expected_ofac = 15.0 / OFAC_CAP
    expected_eia = (2.5 * 0.8) / EIA_NORM
    expected_score = (
        100.0
        * (W_GDELT * expected_gdelt + W_OFAC * expected_ofac + W_EIA * expected_eia)
        / (W_GDELT + W_OFAC + W_EIA)
    )

    assert result.gdelt_component == pytest.approx(expected_gdelt)
    assert result.ofac_component == pytest.approx(expected_ofac)
    assert result.eia_component == pytest.approx(expected_eia)
    assert result.score == pytest.approx(expected_score)
    assert result.contributing_signal_ids == [1, 2, 3, 4, 5, 6, 7]


def test_recency_decay_reduces_stale_contributions():
    fresh = compute_score([feat(1, "gdelt", 5.0, 1.0, hours_ago=0)], NOW)
    day_old = compute_score([feat(1, "gdelt", 5.0, 1.0, hours_ago=24)], NOW)

    assert day_old.gdelt_component == pytest.approx(fresh.gdelt_component * 0.5)
    assert day_old.score < fresh.score


def test_missing_sources_are_renormalized():
    ofac_only = compute_score([feat(1, "ofac_sdn", 3.0) for _ in range(5)], NOW)
    assert ofac_only.score == pytest.approx(60.0)

    everything = compute_score(
        [
            feat(1, "gdelt", 2.0),
            *[feat(i, "ofac_sdn", 3.0) for i in range(2, 7)],
            feat(7, "eia_price", 2.5, 0.8),
        ],
        NOW,
    )
    assert everything.score == pytest.approx(27.0)


def test_score_always_bounded_zero_to_hundred():
    absurd = (
        [feat(i, "gdelt", 5.0) for i in range(500)]
        + [feat(i, "ofac_sdn", 5.0) for i in range(500, 3000)]
        + [feat(i, "eia_price", 5.0) for i in range(3000, 5000)]
    )
    result = compute_score(absurd, NOW)
    assert 0.0 <= result.score <= 100.0
