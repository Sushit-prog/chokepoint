from statistics import median

from sqlalchemy import exists, select

from src.config import load_settings
from src.db.models import ExtractedFeature, Signal
from src.db.session import SessionLocal
from src.extraction.schema import ExtractedFeature as FeatureSchema

MODEL_USED = "rule_based"
EVENT_TYPE = "price_movement"

MAX_SEVERITY = 5.0
BASELINE_FLOOR_PCT = 1.0


def _base_feature(severity: float) -> FeatureSchema:
    return FeatureSchema(
        event_type=EVENT_TYPE,
        severity=round(severity, 4),
        confidence=1.0,
    )


def compute_eia_features(
    price_points: list[dict],
    baseline_days: int = 30,
    vol_scale: float = 1.0,
) -> dict[str, FeatureSchema]:
    points = sorted(
        (
            p
            for p in price_points
            if p.get("date") is not None and p.get("price") is not None
        ),
        key=lambda p: str(p["date"]),
    )
    features: dict[str, FeatureSchema] = {}
    prev_price: float | None = None
    abs_history: list[float] = []
    for point in points:
        date = str(point["date"])
        price = float(point["price"])
        if prev_price is None or prev_price == 0:
            features[date] = _base_feature(0.0)
        else:
            pct_change = (price - prev_price) / prev_price * 100.0
            trailing = (
                abs_history[-baseline_days:] if baseline_days > 0 else abs_history
            )
            baseline = median(trailing) if trailing else 0.0
            denominator = max(baseline, BASELINE_FLOOR_PCT)
            severity = min(MAX_SEVERITY, abs(pct_change) / denominator * vol_scale)
            features[date] = _base_feature(severity)
            abs_history.append(abs(pct_change))
        prev_price = price
    return features


def fetch_pending(session, window: tuple):
    window_start, window_end = window
    stmt = (
        select(Signal)
        .where(
            Signal.source == "eia_price",
            Signal.corridor == "hormuz",
            Signal.commodity == "crude_oil",
            Signal.fetched_at >= window_start,
            Signal.fetched_at < window_end,
            ~exists(
                select(ExtractedFeature.id).where(
                    ExtractedFeature.signal_id == Signal.id
                )
            ),
        )
        .order_by(Signal.id)
    )
    return session.execute(stmt).scalars().all()


def write_feature(
    signal_id: int, feature: FeatureSchema, *, session_factory=SessionLocal
):
    with session_factory() as session:
        session.add(
            ExtractedFeature(
                signal_id=signal_id,
                event_type=feature.event_type,
                severity=feature.severity,
                confidence=feature.confidence,
                model_used=MODEL_USED,
            )
        )
        session.commit()


def run_pending(
    window: tuple,
    *,
    session_factory=SessionLocal,
    settings=None,
) -> tuple[int, int]:
    settings = settings or load_settings()
    with session_factory() as session:
        pending = fetch_pending(session, window)
        if not pending:
            return 0, 0
        all_points = [
            {"date": payload.get("date"), "price": payload.get("price")}
            for (payload,) in session.execute(
                select(Signal.raw_payload).where(Signal.source == "eia_price")
            ).all()
            if isinstance(payload, dict)
        ]

    features_by_date = compute_eia_features(
        all_points,
        baseline_days=settings.eia_baseline_days,
        vol_scale=settings.eia_vol_scale,
    )

    processed = 0
    failed = 0
    for signal in pending:
        try:
            payload = signal.raw_payload or {}
            feature = features_by_date.get(
                str(payload.get("date")),
                _base_feature(0.0),
            )
            write_feature(signal.id, feature, session_factory=session_factory)
            processed += 1
        except Exception as exc:
            failed += 1
            print(f"[eia_price] signal {signal.id} extraction failed: {exc}")
    return processed, failed
