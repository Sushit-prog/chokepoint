import logging

import anthropic
from pydantic import ValidationError
from sqlalchemy import exists, select

from src.config import Settings, load_settings
from src.db.models import ExtractedFeature, Signal
from src.db.session import SessionLocal
from src.extraction.schema import EVENT_TYPES, ExtractedFeature as FeatureSchema

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


def tool_definition() -> dict:
    return {
        "name": TOOL_NAME,
        "description": "Record the risk event extracted from a news article.",
        "input_schema": {
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
    }


def build_client(settings: Settings) -> anthropic.Anthropic:
    if not settings.anthropic_api_key:
        raise ExtractionError(
            "ANTHROPIC_API_KEY env var is required for GDELT LLM extraction"
        )
    return anthropic.Anthropic(api_key=settings.anthropic_api_key)


def _validate(raw) -> FeatureSchema:
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
        tools=[tool_definition()],
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


def extract_article_with_retry(client, model: str, article_payload: dict):
    last_error: Exception | None = None
    for attempt in range(MAX_ATTEMPTS):
        try:
            return call_llm(client, model, article_payload)
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
    client = client or build_client(settings)

    processed = 0
    failed = 0
    for signal in pending:
        try:
            feature, model_used = extract_article_with_retry(
                client,
                settings.anthropic_model,
                signal.raw_payload,
            )
            write_feature(
                signal.id, feature, model_used, session_factory=session_factory
            )
            processed += 1
        except Exception as exc:
            failed += 1
            logger.error("[gdelt] signal %d extraction failed: %s", signal.id, exc)
    return processed, failed
