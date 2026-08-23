from datetime import timedelta

import httpx

from src.config import Settings, load_settings
from src.ingestion.common import (
    SOURCE_EIA_PRICE,
    SourceFailure,
    build_http_client,
    fetch_with_retry,
    run_source,
)

SERIES_ID = "RWTC"


def build_params(window: tuple, api_key: str) -> dict:
    window_start, window_end = window
    return {
        "api_key": api_key,
        "frequency": "daily",
        "data[0]": "value",
        "facets[series][]": SERIES_ID,
        "start": window_start.date().isoformat(),
        "end": _end_date(window_end),
        "length": "5000",
        "sort[0][column]": "period",
        "sort[0][direction]": "asc",
    }


def _end_date(window_end) -> str:
    return (window_end - timedelta(days=1)).date().isoformat()


def parse_prices(payload: dict) -> list[dict]:
    data = (payload.get("response") or {}).get("data") or []
    points: list[dict] = []
    for item in data:
        period = item.get("period")
        value = item.get("value")
        if period is None or value is None:
            continue
        points.append(
            {
                "date": period,
                "price": value,
                "series_id": item.get("seriesId", SERIES_ID),
            }
        )
    return points


def fetch_prices(
    window: tuple,
    client: httpx.Client,
    settings: Settings,
) -> list[dict]:
    if not settings.eia_api_key:
        raise SourceFailure("EIA_API_KEY env var is required for the eia_price source")
    params = build_params(window, settings.eia_api_key)
    response = fetch_with_retry(
        client,
        settings.eia_base_url,
        params=params,
        retry_delay_seconds=settings.retry_delay_seconds,
    )
    try:
        payload = response.json()
    except ValueError as exc:
        raise SourceFailure(f"EIA returned non-JSON response: {exc}") from exc
    if not isinstance(payload, dict):
        raise SourceFailure("EIA returned an unexpected response shape")
    error = payload.get("error")
    if error:
        raise SourceFailure(f"EIA API error: {error}")
    return parse_prices(payload)


def ingest(window: tuple, *, client: httpx.Client | None = None):
    settings = load_settings()
    client = client or build_http_client(settings)

    def _fetch(w: tuple) -> list[dict]:
        return fetch_prices(w, client, settings)

    return run_source(
        source=SOURCE_EIA_PRICE,
        window=window,
        fetch_fn=_fetch,
        corridor=settings.corridor,
        commodity=settings.commodity,
    )
