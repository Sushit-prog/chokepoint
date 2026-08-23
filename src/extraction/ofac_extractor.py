from sqlalchemy import exists, select

from src.db.models import ExtractedFeature, Signal
from src.db.session import SessionLocal
from src.extraction.schema import ExtractedFeature as FeatureSchema

MODEL_USED = "rule_based"

SEVERITY_BY_SDN_TYPE = {
    "vessel": 4.5,
    "entity": 3.0,
    "individual": 2.0,
}
DEFAULT_SEVERITY = 2.5

EVENT_TYPE = "sanctions_listing"


def severity_for(sdn_type: str | None) -> float:
    if not sdn_type:
        return DEFAULT_SEVERITY
    return SEVERITY_BY_SDN_TYPE.get(sdn_type.strip().lower(), DEFAULT_SEVERITY)


def extract_feature(signal: Signal) -> FeatureSchema:
    payload = signal.raw_payload or {}
    return FeatureSchema(
        event_type=EVENT_TYPE,
        severity=severity_for(payload.get("sdn_type")),
        confidence=1.0,
    )


def fetch_pending(session, window: tuple):
    window_start, window_end = window
    stmt = (
        select(Signal)
        .where(
            Signal.source == "ofac_sdn",
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


def run_pending(window: tuple, *, session_factory=SessionLocal) -> tuple[int, int]:
    processed = 0
    failed = 0
    with session_factory() as session:
        pending = fetch_pending(session, window)
    for signal in pending:
        try:
            feature = extract_feature(signal)
            write_feature(signal.id, feature, session_factory=session_factory)
            processed += 1
        except Exception as exc:
            failed += 1
            print(f"[ofac_sdn] signal {signal.id} extraction failed: {exc}")
    return processed, failed


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
