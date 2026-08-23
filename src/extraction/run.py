import argparse
import sys
from datetime import datetime, timezone

from src.extraction import eia_extractor, gdelt_extractor, ofac_extractor
from src.ingestion.common import resolve_window


def _gdelt_runner(window):
    return gdelt_extractor.run_pending(window)


def _ofac_runner(window):
    return ofac_extractor.run_pending(window)


def _eia_runner(window):
    return eia_extractor.run_pending(window)


DEFAULT_SOURCES = (
    ("gdelt", _gdelt_runner),
    ("ofac_sdn", _ofac_runner),
    ("eia_price", _eia_runner),
)


def run_all(window: tuple, sources=None) -> list[tuple[str, int, int, str | None]]:
    sources = sources if sources is not None else DEFAULT_SOURCES
    results = []
    for name, runner in sources:
        try:
            processed, failed = runner(window)
            results.append((name, processed, failed, None))
        except Exception as exc:
            print(f"[{name}] source failed: {exc}")
            results.append((name, 0, 0, str(exc)))
    return results


def report(results) -> None:
    for name, processed, failed, error in results:
        line = f"[{name}] processed:{processed} failed:{failed}"
        if error:
            line += f" (error: {error})"
        print(line)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m src.extraction.run",
        description="Extract risk features from ingested signals for a window.",
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

    print(f"Extraction window: {window[0].isoformat()} -> {window[1].isoformat()}")
    results = run_all(window)
    report(results)

    any_source_error = any(error is not None for _, _, _, error in results)
    any_failures = any(failed > 0 for _, _, failed, _ in results)
    if any_source_error or any_failures:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
