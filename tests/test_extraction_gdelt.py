import pytest
from pydantic import ValidationError
from sqlalchemy import select

from datetime import datetime, timezone

from src.config import load_settings
from src.db.models import ExtractedFeature as ExtractedFeatureRow
from src.db.session import SessionLocal
from src.extraction.gdelt_extractor import (
    ExtractionError,
    build_client,
    extract_article_with_retry,
    run_pending,
)
from src.extraction.schema import ExtractedFeature

VALID = {"event_type": "supply_disruption", "severity": 4.0, "confidence": 0.9}
INVALID = {"event_type": "explosion", "severity": 99.0, "confidence": 2.0}


class StubBlock:
    def __init__(self, type_, input_):
        self.type = type_
        self.input = input_


class StubResponse:
    def __init__(self, content, model="claude-haiku-4-5-20251001"):
        self.content = content
        self.model = model


class StubMessages:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if not self.responses:
            raise AssertionError("unexpected extra LLM call")
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class StubClient:
    def __init__(self, responses):
        self.messages = StubMessages(responses)


def _tool_response(feature_input):
    return StubResponse([StubBlock("tool_use", feature_input)])


def test_schema_accepts_valid_feature():
    feature = ExtractedFeature(**VALID)
    assert feature.event_type == "supply_disruption"
    assert feature.severity == 4.0
    assert feature.confidence == 0.9


@pytest.mark.parametrize(
    "bad_field",
    [
        {"event_type": "explosion", "severity": 1.0, "confidence": 0.5},
        {"event_type": "price_movement", "severity": 5.5, "confidence": 0.5},
        {"event_type": "price_movement", "severity": -0.1, "confidence": 0.5},
        {"event_type": "price_movement", "severity": 1.0, "confidence": 1.5},
        {"event_type": "price_movement", "severity": 1.0},
    ],
)
def test_schema_rejects_invalid_features(bad_field):
    with pytest.raises(ValidationError):
        ExtractedFeature(**bad_field)


def test_build_client_requires_api_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(ExtractionError):
        build_client(load_settings())


def test_successful_extraction_writes_row(sample_window, make_signal):
    signal_id = make_signal(
        "gdelt",
        {"url": "https://example.com/a", "title": "Strait closure feared"},
        fetched_at=datetime(2026, 8, 23, 10, tzinfo=timezone.utc),
    )
    stub = StubClient([_tool_response(VALID)])

    processed, failed = run_pending(sample_window, client=stub)

    assert (processed, failed) == (1, 0)
    call_kwargs = stub.messages.calls[0]
    assert call_kwargs["model"] == load_settings().anthropic_model
    assert call_kwargs["tool_choice"] == {
        "type": "tool",
        "name": "record_extracted_feature",
    }
    assert any(
        tool["name"] == "record_extracted_feature" for tool in call_kwargs["tools"]
    )

    with SessionLocal() as session:
        row = session.execute(select(ExtractedFeatureRow)).scalars().one()
    assert row.signal_id == signal_id
    assert row.event_type == "supply_disruption"
    assert row.severity == pytest.approx(4.0)
    assert row.confidence == pytest.approx(0.9)
    assert row.model_used == "claude-haiku-4-5-20251001"


def test_retry_once_on_invalid_then_success(sample_window, make_signal):
    make_signal("gdelt", {"url": "https://example.com/b", "title": "Sanctions expand"})
    stub = StubClient([_tool_response(INVALID), _tool_response(VALID)])

    processed, failed = run_pending(sample_window, client=stub)

    assert (processed, failed) == (1, 0)
    assert len(stub.messages.calls) == 2


def test_failure_isolation_bad_article_does_not_block_others(
    sample_window, make_signal
):
    for i in range(3):
        make_signal("gdelt", {"url": f"https://example.com/{i}", "title": f"T{i}"})
    stub = StubClient(
        [
            _tool_response(INVALID),
            _tool_response(INVALID),
            _tool_response(
                {"event_type": "shipping_incident", "severity": 2.0, "confidence": 0.7}
            ),
            _tool_response({"event_type": "other", "severity": 1.0, "confidence": 0.6}),
        ]
    )

    processed, failed = run_pending(sample_window, client=stub)

    assert (processed, failed) == (2, 1)
    assert len(stub.messages.calls) == 4

    with SessionLocal() as session:
        rows = session.execute(select(ExtractedFeatureRow)).scalars().all()
    assert len(rows) == 2


def test_rerun_skips_already_processed_signals(sample_window, make_signal):
    make_signal("gdelt", {"url": "https://example.com/c", "title": "Rerun check"})
    first = StubClient([_tool_response(VALID)])
    run_pending(sample_window, client=first)

    second = StubClient([])
    processed, failed = run_pending(sample_window, client=second)

    assert (processed, failed) == (0, 0)
    assert second.messages.calls == []


def test_extract_article_with_retry_exhausts_attempts():
    stub = StubClient([_tool_response(INVALID), _tool_response(INVALID)])
    with pytest.raises(ExtractionError):
        extract_article_with_retry(stub, "claude-haiku-4-5", {"title": "x", "url": "y"})
    assert len(stub.messages.calls) == 2
