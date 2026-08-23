import httpx
from sqlalchemy import select

from src.db.models import Signal
from src.db.session import SessionLocal
from src.ingestion.ofac import ingest, parse_sdn_csv


SAMPLE_CSV = """123,"National Iranian Oil Company","entity","IRAN",-0-,-0-,-0-,-0-,-0-,-0-,-0-,-0-
124,"Hormuz Shipping Lines","vessel","NPWMD; IRAN",-0-,HRCN,-0-,15000,12000,PA,Owner A,-0-
125,"Unrelated Entity","individual","SDGT",-0-,-0-,-0-,-0-,-0-,-0-,-0-,-0-
126,"Iran Program Person","individual","IRAN",Broker,-0-,-0-,-0-,-0-,-0-,-0-,DOB 01 Jan 1970
"""


def test_parse_sdn_csv_filters_to_iran_program():
    entries = parse_sdn_csv(SAMPLE_CSV)
    names = [entry["sdn_name"] for entry in entries]
    assert names == [
        "National Iranian Oil Company",
        "Hormuz Shipping Lines",
        "Iran Program Person",
    ]


def test_parse_sdn_csv_normalizes_null_tokens():
    entries = parse_sdn_csv(SAMPLE_CSV)
    vessel = next(e for e in entries if e["sdn_type"] == "vessel")
    assert vessel["title"] is None
    assert vessel["call_sign"] == "HRCN"
    assert vessel["program"] == "NPWMD; IRAN"


def test_ofac_ingest_stores_only_iran_rows(sample_window):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host.endswith("treasury.gov")
        return httpx.Response(200, text=SAMPLE_CSV)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    outcome = ingest(sample_window, client=client)

    assert outcome.status == "completed"
    assert outcome.row_count == 3

    with SessionLocal() as session:
        rows = (
            session.execute(select(Signal).where(Signal.source == "ofac_sdn"))
            .scalars()
            .all()
        )
    assert len(rows) == 3
    for row in rows:
        assert row.corridor == "hormuz"
        assert row.commodity == "crude_oil"
        assert "IRAN" in row.raw_payload["program"].upper()
        assert row.raw_payload["ent_num"] in {"123", "124", "126"}
