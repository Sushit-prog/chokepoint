import json
import logging

import httpx

from src.config import PROVIDER_BASE_URLS, Settings
from src.extraction.schema import EVENT_TYPES

logger = logging.getLogger("chokepoint.extraction")

TOOL_NAME = "record_extracted_feature"

SYSTEM_PROMPT = (
    "You are an energy supply chain risk analyst monitoring the Strait of "
    "Hormuz corridor for crude oil supply risk. Classify the news article by "
    "calling the record_extracted_feature tool exactly once.\n"
    "Guidance:\n"
    "- event_type: pick the closest match; use 'other' only when nothing fits.\n"
    "- severity: 0 (irrelevant) to 5 (catastrophic, e.g. strait closure). "
    "Routine commentary scores 1-2.\n"
    "- confidence: how certain the classification and severity are, 0 to 1."
)

MAX_ATTEMPTS = 2


class ExtractionError(Exception):
    pass


def build_tool_definition() -> dict:
    return {
        "type": "function",
        "function": {
            "name": TOOL_NAME,
            "description": "Record the risk event extracted from a news article.",
            "parameters": {
                "type": "object",
                "properties": {
                    "event_type": {
                        "type": "string",
                        "enum": list(EVENT_TYPES),
                        "description": "The risk event category.",
                    },
                    "severity": {
                        "type": "number",
                        "minimum": 0,
                        "maximum": 5,
                        "description": "Supply-risk severity on a 0-5 scale.",
                    },
                    "confidence": {
                        "type": "number",
                        "minimum": 0,
                        "maximum": 1,
                        "description": "Confidence in the extraction, 0-1.",
                    },
                },
                "required": ["event_type", "severity", "confidence"],
                "additionalProperties": False,
            },
        },
    }


def _user_content(article_payload: dict) -> str:
    title = str((article_payload or {}).get("title", ""))
    url = str((article_payload or {}).get("url", ""))
    return f"Classify this article.\nTitle: {title}\nURL: {url}"


def post_chat_completion(
    client: httpx.Client,
    settings: Settings,
    article_payload: dict,
) -> tuple[dict, str]:
    base_url = PROVIDER_BASE_URLS.get(settings.llm_provider)
    if not base_url:
        raise ExtractionError(f"unsupported LLM_PROVIDER: {settings.llm_provider}")
    if not settings.llm_api_key:
        raise ExtractionError(
            f"LLM_API_KEY env var is required for provider {settings.llm_provider}"
        )

    payload = {
        "model": settings.llm_model,
        "max_tokens": 300,
        "temperature": 0,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": _user_content(article_payload)},
        ],
        "tools": [build_tool_definition()],
        "tool_choice": {"type": "function", "function": {"name": TOOL_NAME}},
    }

    response = None
    last_error: str | Exception | None = None
    for attempt in range(MAX_ATTEMPTS):
        try:
            response = client.post(
                f"{base_url}/chat/completions",
                headers={"Authorization": f"Bearer {settings.llm_api_key}"},
                json=payload,
            )
        except httpx.HTTPError as exc:
            last_error = exc
            continue
        if response.status_code < 400:
            break
        if response.status_code < 500:
            raise ExtractionError(
                f"LLM API returned HTTP {response.status_code}: {response.text[:300]}"
            )
        last_error = f"HTTP {response.status_code}"
        response = None

    if response is None:
        raise ExtractionError(
            f"LLM request failed after {MAX_ATTEMPTS} attempts: {last_error}"
        )

    try:
        body = response.json()
    except ValueError as exc:
        raise ExtractionError(f"LLM API returned non-JSON body: {exc}") from exc

    return parse_completion(body, settings)


def parse_completion(body: dict, settings: Settings) -> tuple[dict, str]:
    choices = body.get("choices") or []
    message = choices[0].get("message", {}) if choices else {}
    tool_calls = message.get("tool_calls") or []
    if not tool_calls:
        finish_reason = (
            choices[0].get("finish_reason") if choices else "missing_choices"
        )
        raise ExtractionError(
            f"model returned no tool call (finish_reason={finish_reason})"
        )
    arguments = tool_calls[0].get("function", {}).get("arguments")
    try:
        raw = json.loads(arguments) if isinstance(arguments, str) else arguments
    except (TypeError, ValueError) as exc:
        raise ExtractionError(f"tool arguments were not valid JSON: {exc}") from exc
    model_used = body.get("model") or settings.llm_model
    return raw, model_used


def create_generic_caller(client: httpx.Client, settings: Settings):
    def _caller(article_payload: dict) -> tuple[dict, str]:
        return post_chat_completion(client, settings, article_payload)

    return _caller
