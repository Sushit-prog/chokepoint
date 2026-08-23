import csv
import io

import httpx

from src.config import Settings, load_settings
from src.ingestion.common import (
    SOURCE_OFAC_SDN,
    build_http_client,
    fetch_with_retry,
    run_source,
)

SDN_COLUMNS = [
    "ent_num",
    "sdn_name",
    "sdn_type",
    "program",
    "title",
    "call_sign",
    "vess_type",
    "tonnage",
    "grt",
    "vess_flag",
    "vess_owner",
    "remarks",
]

NULL_TOKEN = "-0-"
PROGRAM_FILTER = "IRAN"


def _clean(value: str) -> str | None:
    value = value.strip().lstrip("\ufeff").strip('"')
    if not value or value == NULL_TOKEN:
        return None
    return value


def parse_sdn_csv(text: str) -> list[dict]:
    entries: list[dict] = []
    for record in csv.reader(io.StringIO(text)):
        if not any(field.strip() for field in record):
            continue
        padded = record + [""] * (len(SDN_COLUMNS) - len(record))
        row = {name: _clean(value) for name, value in zip(SDN_COLUMNS, padded)}
        program = row.get("program") or ""
        if PROGRAM_FILTER in program.upper():
            entries.append(row)
    return entries


def fetch_iran_entries(
    window: tuple,
    client: httpx.Client,
    settings: Settings,
) -> list[dict]:
    response = fetch_with_retry(
        client,
        settings.ofac_sdn_url,
        retry_delay_seconds=settings.retry_delay_seconds,
    )
    return parse_sdn_csv(response.text)


def ingest(window: tuple, *, client: httpx.Client | None = None):
    settings = load_settings()
    client = client or build_http_client(settings)

    def _fetch(w: tuple) -> list[dict]:
        return fetch_iran_entries(w, client, settings)

    return run_source(
        source=SOURCE_OFAC_SDN,
        window=window,
        fetch_fn=_fetch,
        corridor=settings.corridor,
        commodity=settings.commodity,
    )
