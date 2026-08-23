from typing import Literal

from pydantic import BaseModel, Field

EventType = Literal[
    "supply_disruption",
    "shipping_incident",
    "geopolitical_tension",
    "sanctions_listing",
    "price_movement",
    "other",
]

EVENT_TYPES: tuple[str, ...] = (
    "supply_disruption",
    "shipping_incident",
    "geopolitical_tension",
    "sanctions_listing",
    "price_movement",
    "other",
)


class ExtractedFeature(BaseModel):
    event_type: EventType
    severity: float = Field(ge=0.0, le=5.0)
    confidence: float = Field(ge=0.0, le=1.0)
