import argparse
import sys
from datetime import datetime, timezone

from src.ingestion.common import SourceOutcome, resolve_window
from src.ingestion.eia import ingest as ingest_eia
from src.ingestion.gdelt import ingest as ingest_gdelt
from src.ingestion.ofac import ingest as ingest_ofac

DEFAULT_SOURCES = (ingest_gdelt, ingest_ofac, ingest_eia)


def run_all(window: tuple, sources=None) -> list[SourceOutcome]:
    sources = sources if sources is not None else DEFAULT_SOURCES
    outcomes: list[SourceOutcome] = []
    for ingest_fn in sources:
        try:
            outcomes.append(ingest_fn(window))
        except Exception as exc:
            name = getattr(ingest_fn, "__name__", "unknown")
            print(f"[{name}] crashed unexpectedly: {exc}")
            outcomes.append(SourceOutcome(name, "failed", error=str(exc)))
    return outcomes


def report(outcomes: list[SourceOutcome]) -> None:
    for outcome in outcomes:
        line = f"[{outcome.source}] {outcome.status}"
        if outcome.status == "completed":
            line += f": {outcome.row_count} signals stored"
        elif outcome.error:
            line += f": {outcome.error}"
        print(line)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m src.ingestion.run",
        description="Ingest GDELT, OFAC SDN, and EIA crude price signals into Postgres.",
    )
    parser.add_argument(
        "--window-days",
        type=int,
        default=1,
        help="Length of the ingestion window in days; ends at the most recent UTC midnight.",
    )
    parser.add_argument(
        "--window-start",
        type=str,
        default=None,
        help="Explicit ISO-8601 window start (overrides --window-days bucketing).",
    )
    parser.add_argument(
        "--window-end",
        type=str,
        default=None,
        help="Explicit ISO-8601 window end (must be provided together with --window-start).",
    )
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

    print(f"Ingestion window: {window[0].isoformat()} -> {window[1].isoformat()}")
    outcomes = run_all(window)
    report(outcomes)

    if any(outcome.status == "failed" for outcome in outcomes):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
