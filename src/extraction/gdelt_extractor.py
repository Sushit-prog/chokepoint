import logging

import anthropic
from pydantic import ValidationError
from sqlalchemy import exists, select

from src.config import PROVIDER_BASE_URLS, Settings, load_settings
from src.db.models import ExtractedFeature, Signal
from src.db.session import SessionLocal
from src.extraction.llm_client import (
    MAX_ATTEMPTS,
    TOOL_NAME,
    ExtractionError,
    SYSTEM_PROMPT,
    build_tool_definition,
    create_generic_caller,
)
from src.ingestion.common import build_http_client
from src.extraction.schema import ExtractedFeature as FeatureSchema

logger = logging.getLogger("chokepoint.extraction")


def _anthropic_tool() -> dict:
    inner = build_tool_definition()["function"]
    return {
        "name": inner["name"],
        "description": inner["description"],
        "input_schema": inner["parameters"],
    }


def build_client(settings: Settings) -> anthropic.Anthropic:
    if not settings.anthropic_api_key:
        raise ExtractionError(
            "ANTHROPIC_API_KEY env var is required for GDELT LLM extraction "
            "when LLM_PROVIDER=anthropic"
        )
    return anthropic.Anthropic(api_key=settings.anthropic_api_key)


def _validate(raw):
    return FeatureSchema.model_validate(raw)


def call_llm(client, model: str, article_payload: dict) -> tuple[FeatureSchema, str]:
    title = str((article_payload or {}).get("title", ""))
    url = str((article_payload or {}).get("url", ""))
    response = client.messages.create(
        model=model,
        max_tokens=300,
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": f"Classify this article.\nTitle: {title}\nURL: {url}",
            }
        ],
        tools=[_anthropic_tool()],
        tool_choice={"type": "tool", "name": TOOL_NAME},
    )
    tool_block = next(
        (
            block
            for block in response.content
            if getattr(block, "type", None) == "tool_use"
        ),
        None,
    )
    if tool_block is None:
        raise ExtractionError("model returned no tool_use block")
    feature = _validate(tool_block.input)
    model_used = getattr(response, "model", None) or model
    return feature, model_used


def extract_article_with_retry(caller, article_payload: dict):
    last_error: Exception | None = None
    for attempt in range(MAX_ATTEMPTS):
        try:
            raw, model_used = caller(article_payload)
            return _validate(raw), model_used
        except (ValidationError, ExtractionError) as exc:
            last_error = exc
            logger.warning(
                "extraction attempt %d/%d failed validation: %s",
                attempt + 1,
                MAX_ATTEMPTS,
                exc,
            )
    raise ExtractionError(
        f"schema validation failed after {MAX_ATTEMPTS} attempts: {last_error}"
    )


def fetch_pending(session, window: tuple):
    window_start, window_end = window
    stmt = (
        select(Signal)
        .where(
            Signal.source == "gdelt",
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


def write_feature(
    signal_id: int,
    feature: FeatureSchema,
    model_used: str,
    *,
    session_factory=SessionLocal,
):
    with session_factory() as session:
        session.add(
            ExtractedFeature(
                signal_id=signal_id,
                event_type=feature.event_type,
                severity=feature.severity,
                confidence=feature.confidence,
                model_used=model_used,
            )
        )
        session.commit()


def make_caller(settings: Settings, client=None):
    if settings.llm_provider == "anthropic":
        anthropic_client = client or build_client(settings)
        model = settings.anthropic_model
        return lambda payload: call_llm(anthropic_client, model, payload)
    if settings.llm_provider not in PROVIDER_BASE_URLS:
        raise ExtractionError(f"unsupported LLM_PROVIDER: {settings.llm_provider}")
    if not settings.llm_api_key:
        raise ExtractionError(
            f"LLM_API_KEY env var is required for provider {settings.llm_provider}"
        )
    http_client = client or build_http_client(settings)
    return create_generic_caller(http_client, settings)


def run_pending(
    window: tuple,
    *,
    client=None,
    session_factory=SessionLocal,
    settings: Settings | None = None,
) -> tuple[int, int]:
    settings = settings or load_settings()
    with session_factory() as session:
        pending = fetch_pending(session, window)
    if not pending:
        return 0, 0
    caller = make_caller(settings, client)

    processed = 0
    failed = 0
    for signal in pending:
        try:
            feature, model_used = extract_article_with_retry(caller, signal.raw_payload)
            write_feature(
                signal.id, feature, model_used, session_factory=session_factory
            )
            processed += 1
        except Exception as exc:
            failed += 1
            logger.error("[gdelt] signal %d extraction failed: %s", signal.id, exc)
    return processed, failed
