import json

import httpx
import pytest
from sqlalchemy import select

from src.config import load_settings
from src.db.models import ExtractedFeature as ExtractedFeatureRow
from src.db.session import SessionLocal
from src.extraction.gdelt_extractor import run_pending
from src.extraction.llm_client import (
    ExtractionError,
    post_chat_completion,
)


VALID_ARGS = {"event_type": "supply_disruption", "severity": 4.2, "confidence": 0.85}


def _completion_body(arguments, model="llama-3.3-70b-versatile"):
    return {
        "model": model,
        "choices": [
            {
                "finish_reason": "tool_calls",
                "message": {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "type": "function",
                            "function": {
                                "name": "record_extracted_feature",
                                "arguments": arguments,
                            },
                        }
                    ],
                },
            }
        ],
    }


def _settings(monkeypatch, **overrides):
    monkeypatch.setenv("LLM_PROVIDER", overrides.get("provider", "groq"))
    monkeypatch.setenv("LLM_API_KEY", overrides.get("key", "gsk_test_key"))
    monkeypatch.setenv("LLM_MODEL", overrides.get("model", "llama-3.3-70b-versatile"))
    return load_settings()


def test_request_shape_forces_tool_and_schema(monkeypatch):
    settings = _settings(monkeypatch)
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("Authorization")
        captured["body"] = json.loads(request.content.decode())
        return httpx.Response(200, json=_completion_body(json.dumps(VALID_ARGS)))

    client = httpx.Client(transport=httpx.MockTransport(handler))
    raw, model_used = post_chat_completion(client, settings, {"title": "T", "url": "U"})

    assert raw == VALID_ARGS
    assert model_used == "llama-3.3-70b-versatile"
    assert captured["url"] == "https://api.groq.com/openai/v1/chat/completions"
    assert captured["auth"] == "Bearer gsk_test_key"
    body = captured["body"]
    assert body["model"] == "llama-3.3-70b-versatile"
    assert body["temperature"] == 0
    tool = body["tools"][0]
    assert tool["function"]["name"] == "record_extracted_feature"
    enum = tool["function"]["parameters"]["properties"]["event_type"]["enum"]
    assert "supply_disruption" in enum and len(enum) == 6
    assert body["tool_choice"] == {
        "type": "function",
        "function": {"name": "record_extracted_feature"},
    }


def test_missing_tool_call_raises(monkeypatch):
    settings = _settings(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "model": "m",
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": "nope"},
                    }
                ],
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(ExtractionError):
        post_chat_completion(client, settings, {"title": "T"})


def test_malformed_json_arguments_raise(monkeypatch):
    settings = _settings(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_completion_body("{not valid json"))

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(ExtractionError):
        post_chat_completion(client, settings, {"title": "T"})


def test_client_error_fails_without_retry(monkeypatch):
    settings = _settings(monkeypatch)
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(401, text="invalid api key")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(ExtractionError):
        post_chat_completion(client, settings, {"title": "T"})
    assert len(calls) == 1


def test_server_error_retries_once_then_succeeds(monkeypatch):
    settings = _settings(monkeypatch)
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(503, text="overloaded")
        return httpx.Response(200, json=_completion_body(json.dumps(VALID_ARGS)))

    client = httpx.Client(transport=httpx.MockTransport(handler))
    raw, _ = post_chat_completion(client, settings, {"title": "T"})
    assert raw == VALID_ARGS
    assert len(calls) == 2


def test_server_error_exhausts_attempts(monkeypatch):
    settings = _settings(monkeypatch)
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(500, text="down")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(ExtractionError):
        post_chat_completion(client, settings, {"title": "T"})
    assert len(calls) == 2


def test_run_pending_groq_path_writes_rows(sample_window, make_signal, monkeypatch):
    _settings(monkeypatch)
    make_signal(
        "gdelt",
        {"url": "https://example.com/groq-1", "title": "Strait of Hormuz attack"},
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=_completion_body(
                json.dumps(
                    {
                        "event_type": "shipping_incident",
                        "severity": 3.5,
                        "confidence": 0.7,
                    }
                ),
                model="llama-3.3-70b-versatile",
            ),
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    processed, failed = run_pending(sample_window, client=client)

    assert (processed, failed) == (1, 0)
    with SessionLocal() as session:
        row = session.execute(select(ExtractedFeatureRow)).scalars().one()
    assert row.model_used == "llama-3.3-70b-versatile"
    assert row.event_type == "shipping_incident"


def test_run_pending_groq_without_key_fails_source(
    monkeypatch, sample_window, make_signal
):
    monkeypatch.setenv("LLM_PROVIDER", "groq")
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    make_signal("gdelt", {"url": "https://example.com/x", "title": "t"})

    with pytest.raises(Exception) as excinfo:
        run_pending(sample_window, client=None)
    assert "LLM_API_KEY" in str(excinfo.value)
