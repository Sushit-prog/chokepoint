import math
from dataclasses import dataclass, field
from datetime import datetime, timezone

SOURCE_GDELT = "gdelt"
SOURCE_OFAC_SDN = "ofac_sdn"
SOURCE_EIA_PRICE = "eia_price"

W_GDELT = 0.5
W_OFAC = 0.3
W_EIA = 0.2

DECAY_HALF_LIFE_HOURS = 24.0
GDELT_NORM = 20.0
OFAC_CAP = 25.0
EIA_NORM = 10.0


@dataclass
class FeatureInput:
    signal_id: int
    source: str
    severity: float
    confidence: float
    timestamp: datetime


@dataclass
class ScoreResult:
    score: float | None
    contributing_signal_ids: list[int] = field(default_factory=list)
    gdelt_component: float | None = None
    ofac_component: float | None = None
    eia_component: float | None = None


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def decay_factor(hours_since: float) -> float:
    return math.exp(-math.log(2.0) * max(hours_since, 0.0) / DECAY_HALF_LIFE_HOURS)


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def compute_score(features, now: datetime) -> ScoreResult:
    now = _ensure_utc(now)

    gdelt_sum = 0.0
    ofac_sum = 0.0
    eia_sum = 0.0
    gdelt_ids: list[int] = []
    ofac_ids: list[int] = []
    eia_ids: list[int] = []

    for feature in features:
        hours_since = (now - _ensure_utc(feature.timestamp)).total_seconds() / 3600.0
        weight = decay_factor(hours_since)
        contribution = feature.severity * feature.confidence * weight
        if feature.source == SOURCE_GDELT:
            gdelt_sum += contribution
            gdelt_ids.append(feature.signal_id)
        elif feature.source == SOURCE_OFAC_SDN:
            ofac_sum += contribution
            ofac_ids.append(feature.signal_id)
        elif feature.source == SOURCE_EIA_PRICE:
            eia_sum += contribution
            eia_ids.append(feature.signal_id)

    gdelt_component = _clamp01(gdelt_sum / GDELT_NORM)
    ofac_component = min(ofac_sum, OFAC_CAP) / OFAC_CAP
    eia_component = _clamp01(eia_sum / EIA_NORM)

    components = (
        (W_GDELT, gdelt_component, gdelt_ids),
        (W_OFAC, ofac_component, ofac_ids),
        (W_EIA, eia_component, eia_ids),
    )

    weighted_total = 0.0
    weight_present = 0.0
    for weight, component, ids in components:
        if ids:
            weighted_total += weight * component
            weight_present += weight

    if weight_present == 0:
        return ScoreResult(score=None, contributing_signal_ids=[])

    score = 100.0 * weighted_total / weight_present

    all_ids = sorted(set(gdelt_ids + ofac_ids + eia_ids))
    return ScoreResult(
        score=round(score, 4),
        contributing_signal_ids=all_ids,
        gdelt_component=round(gdelt_component, 4) if gdelt_ids else None,
        ofac_component=round(ofac_component, 4) if ofac_ids else None,
        eia_component=round(eia_component, 4) if eia_ids else None,
    )
