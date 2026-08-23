import argparse
import sys
from datetime import datetime, timezone

from sqlalchemy import select

from src.db.models import ExtractedFeature, RiskScore, Signal
from src.db.session import SessionLocal
from src.ingestion.common import resolve_window
from src.scoring.formula import FeatureInput, compute_score


def load_feature_inputs(
    window: tuple, *, session_factory=SessionLocal
) -> list[FeatureInput]:
    window_start, window_end = window
    with session_factory() as session:
        rows = session.execute(
            select(
                Signal.id,
                Signal.source,
                Signal.fetched_at,
                ExtractedFeature.severity,
                ExtractedFeature.confidence,
            )
            .join(ExtractedFeature, ExtractedFeature.signal_id == Signal.id)
            .where(
                Signal.corridor == "hormuz",
                Signal.commodity == "crude_oil",
                Signal.fetched_at >= window_start,
                Signal.fetched_at < window_end,
            )
        ).all()
    return [
        FeatureInput(
            signal_id=row.id,
            source=row.source,
            severity=row.severity,
            confidence=row.confidence,
            timestamp=row.fetched_at,
        )
        for row in rows
    ]


def write_risk_score(
    score_result, corridor: str, commodity: str, *, session_factory=SessionLocal
) -> int:
    with session_factory() as session:
        risk_score = RiskScore(
            corridor=corridor,
            commodity=commodity,
            score=score_result.score,
            contributing_signal_ids=score_result.contributing_signal_ids,
        )
        session.add(risk_score)
        session.commit()
        return risk_score.id


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m src.scoring.run",
        description="Compute the deterministic corridor risk score for a window.",
    )
    parser.add_argument("--window-days", type=int, default=1)
    parser.add_argument("--window-start", type=str, default=None)
    parser.add_argument("--window-end", type=str, default=None)
    args = parser.parse_args(argv)

    start_arg = datetime.fromisoformat(args.window_start) if args.window_start else None
    end_arg = datetime.fromisoformat(args.window_end) if args.window_end else None

    try:
        window = resolve_window(
            datetime.now(timezone.utc), args.window_days, start_arg, end_arg
        )
    except ValueError as exc:
        parser.error(str(exc))
        return 2

    features = load_feature_inputs(window)
    result = compute_score(features, now=datetime.now(timezone.utc))

    if result.score is None:
        print(
            f"[scoring] no extracted features in window {window[0].isoformat()} -> {window[1].isoformat()}; no risk_scores row written"
        )
        return 0

    risk_score_id = write_risk_score(result, "hormuz", "crude_oil")
    print(f"[scoring] risk_scores id={risk_score_id} score={result.score}")
    print(
        f"[scoring] components: gdelt={result.gdelt_component} ofac={result.ofac_component} eia={result.eia_component}"
    )
    print(f"[scoring] contributing signals: {len(result.contributing_signal_ids)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
