from sqlalchemy import select

from src.db.models import ExtractedFeature as ExtractedFeatureRow, Signal
from src.db.session import SessionLocal
from src.extraction.ofac_extractor import (
    DEFAULT_SEVERITY,
    extract_feature,
    run_pending,
    severity_for,
)


def test_severity_rule_orders_vessel_above_entity_above_individual():
    assert severity_for("vessel") > severity_for("entity") > severity_for("individual")


def test_severity_rule_is_case_insensitive_and_has_default():
    assert severity_for("VESSEL") == 4.5
    assert severity_for("Entity") == 3.0
    assert severity_for(None) == DEFAULT_SEVERITY
    assert severity_for("aircraft") == DEFAULT_SEVERITY


def test_extract_feature_tags_rule_based_metadata(make_signal):
    signal_id = make_signal(
        "ofac_sdn",
        {
            "ent_num": "1",
            "sdn_name": "Hormuz Tanker",
            "sdn_type": "vessel",
            "program": "IRAN",
        },
    )
    with SessionLocal() as session:
        signal = session.get(Signal, signal_id)
    feature = extract_feature(signal)
    assert feature.event_type == "sanctions_listing"
    assert feature.severity == 4.5
    assert feature.confidence == 1.0


def test_run_pending_writes_all_pending_rows(sample_window, make_signal):
    make_signal(
        "ofac_sdn",
        {"ent_num": "1", "sdn_name": "A", "sdn_type": "vessel", "program": "IRAN"},
    )
    make_signal(
        "ofac_sdn",
        {"ent_num": "2", "sdn_name": "B", "sdn_type": "entity", "program": "IRAN"},
    )
    make_signal(
        "ofac_sdn",
        {
            "ent_num": "3",
            "sdn_name": "C",
            "sdn_type": "individual",
            "program": "IRAN; IFCA",
        },
    )
    make_signal(
        "ofac_sdn",
        {"ent_num": "4", "sdn_name": "D", "sdn_type": None, "program": "IRAN"},
    )

    processed, failed = run_pending(sample_window)

    assert (processed, failed) == (4, 0)
    with SessionLocal() as session:
        rows = session.execute(select(ExtractedFeatureRow)).scalars().all()
    severities = sorted(row.severity for row in rows)
    assert severities == [2.0, 2.5, 3.0, 4.5]
    assert all(row.model_used == "rule_based" for row in rows)
    assert all(row.event_type == "sanctions_listing" for row in rows)
    assert all(row.confidence == 1.0 for row in rows)


def test_rerun_skips_processed(sample_window, make_signal):
    make_signal(
        "ofac_sdn",
        {"ent_num": "9", "sdn_name": "Z", "sdn_type": "vessel", "program": "IRAN"},
    )
    run_pending(sample_window)
    processed, failed = run_pending(sample_window)
    assert (processed, failed) == (0, 0)


def test_non_corridor_signals_ignored(sample_window, make_signal):
    make_signal(
        "ofac_sdn",
        {"ent_num": "5", "sdn_name": "X", "sdn_type": "vessel", "program": "IRAN"},
        corridor="malacca",
    )
    processed, failed = run_pending(sample_window)
    assert (processed, failed) == (0, 0)
