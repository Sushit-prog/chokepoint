import httpx

from src.config import Settings, load_settings
from src.ingestion.common import (
    SOURCE_GDELT,
    SourceFailure,
    build_http_client,
    fetch_with_retry,
    run_source,
)

QUERY_TERMS = [
    "Strait of Hormuz",
    "Iran oil sanctions",
    "Red Sea shipping crude",
]


def build_query(terms: list[str] | None = None) -> str:
    terms = terms if terms is not None else QUERY_TERMS
    return "(" + " OR ".join(f'"{term}"' for term in terms) + ")"


def build_params(window: tuple, max_records: int) -> dict:
    window_start, window_end = window
    return {
        "query": build_query(),
        "mode": "artlist",
        "format": "json",
        "maxrecords": str(max_records),
        "startdatetime": window_start.strftime("%Y%m%d%H%M%S"),
        "enddatetime": window_end.strftime("%Y%m%d%H%M%S"),
    }


def parse_articles(payload: dict) -> list[dict]:
    articles = payload.get("articles") or []
    seen_urls: set[str] = set()
    unique: list[dict] = []
    for article in articles:
        url = article.get("url")
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        unique.append(article)
    return unique


def fetch_articles(
    window: tuple,
    client: httpx.Client,
    settings: Settings,
) -> list[dict]:
    params = build_params(window, settings.gdelt_max_records)
    response = fetch_with_retry(
        client,
        settings.gdelt_base_url,
        params=params,
        retry_delay_seconds=settings.retry_delay_seconds,
    )
    try:
        payload = response.json()
    except ValueError as exc:
        raise SourceFailure(f"GDELT returned non-JSON response: {exc}") from exc
    if not isinstance(payload, dict):
        raise SourceFailure("GDELT returned an unexpected response shape")
    return parse_articles(payload)


def ingest(window: tuple, *, client: httpx.Client | None = None):
    settings = load_settings()
    client = client or build_http_client(settings)

    def _fetch(w: tuple) -> list[dict]:
        return fetch_articles(w, client, settings)

    return run_source(
        source=SOURCE_GDELT,
        window=window,
        fetch_fn=_fetch,
        corridor=settings.corridor,
        commodity=settings.commodity,
    )
