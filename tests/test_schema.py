from sqlalchemy import inspect

from src.db.models import (
    Alert,
    ExtractedFeature,
    Recommendation,
    ReferenceRoute,
    RiskScore,
    Signal,
)
from src.db.session import engine

EXPECTED_FKS = [
    ("extracted_features", "signal_id", "signals", "id"),
    ("alerts", "risk_score_id", "risk_scores", "id"),
    ("recommendations", "alert_id", "alerts", "id"),
    ("recommendations", "route_id", "reference_routes", "id"),
]


def test_ingestion_jobs_unique_constraint_exists():
    inspector = inspect(engine)
    unique_constraints = inspector.get_unique_constraints("ingestion_jobs")
    matches = [
        uc
        for uc in unique_constraints
        if set(uc["column_names"]) == {"source", "window_start", "window_end"}
    ]
    assert matches, (
        f"expected a unique constraint on (source, window_start, window_end), "
        f"found {unique_constraints}"
    )


def test_all_foreign_keys_resolve():
    inspector = inspect(engine)
    for table, column, ref_table, ref_column in EXPECTED_FKS:
        fks = inspector.get_foreign_keys(table)
        found = any(
            fk["referred_table"] == ref_table
            and fk["referred_columns"] == [ref_column]
            and column in fk["constrained_columns"]
            for fk in fks
        )
        assert found, (
            f"expected {table}.{column} -> {ref_table}.{ref_column}, found {fks}"
        )


def test_orm_relationships_resolve():
    assert ExtractedFeature.signal.property.mapper.class_ is Signal
    assert Alert.risk_score.property.mapper.class_ is RiskScore
    assert Recommendation.alert.property.mapper.class_ is Alert
    assert Recommendation.route.property.mapper.class_ is ReferenceRoute
