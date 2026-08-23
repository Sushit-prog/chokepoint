import httpx
from sqlalchemy import select

from src.db.models import Signal
from src.db.session import SessionLocal
from src.ingestion.gdelt import build_params, build_query, ingest


SAMPLE_RESPONSE = {
    "articles": [
        {
            "url": "https://example.com/hormuz-tension",
            "title": "Tanker traffic slows near the strait",
            "seendate": "20260823T141500Z",
            "domain": "example.com",
            "language": "en",
            "sourcecountry": "us",
        },
        {
            "url": "https://example.com/sanctions-bite",
            "title": "Iran oil sanctions squeeze exports",
            "seendate": "20260823T183000Z",
            "domain": "example.com",
            "language": "en",
            "sourcecountry": "gb",
        },
        {
            "url": "https://example.com/hormuz-tension",
            "title": "Duplicate article should be dropped",
        },
    ]
}


def test_build_query_contains_all_terms():
    query = build_query()
    assert '"Strait of Hormuz"' in query
    assert '"Iran oil sanctions"' in query
    assert '"Red Sea shipping crude"' in query
    assert query.count(" OR ") == 2


def test_gdelt_ingest_creates_signals(sample_window):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, json=SAMPLE_RESPONSE)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    outcome = ingest(sample_window, client=client)

    assert outcome.status == "completed"
    assert outcome.row_count == 2
    assert captured["params"]["mode"] == "artlist"
    assert captured["params"]["format"] == "json"
    assert captured["params"]["startdatetime"] == "20260823000000"
    assert captured["params"]["enddatetime"] == "20260824000000"
    assert '"Strait of Hormuz"' in captured["params"]["query"]

    with SessionLocal() as session:
        rows = (
            session.execute(select(Signal).where(Signal.source == "gdelt"))
            .scalars()
            .all()
        )
    assert len(rows) == 2
    for row in rows:
        assert row.corridor == "hormuz"
        assert row.commodity == "crude_oil"
        assert row.fetched_at is not None
        assert row.raw_payload["domain"] == "example.com"
