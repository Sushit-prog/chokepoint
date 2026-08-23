import os
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, text

_BASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+psycopg://postgres:postgres@localhost:15432/chokepoint",
)
TEST_DB_NAME = "chokepoint_test"
os.environ["DATABASE_URL"] = _BASE_URL.rsplit("/", 1)[0] + "/" + TEST_DB_NAME

from alembic import command
from alembic.config import Config

from src.db.models import Signal
from src.db.session import SessionLocal, engine


def _ensure_database():
    admin_url = _BASE_URL.rsplit("/", 1)[0] + "/postgres"
    admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with admin_engine.connect() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM pg_database WHERE datname = :name"),
            {"name": TEST_DB_NAME},
        ).scalar()
        if not exists:
            conn.execute(text(f'CREATE DATABASE "{TEST_DB_NAME}"'))
    admin_engine.dispose()


@pytest.fixture(scope="session", autouse=True)
def _migrated_db():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _ensure_database()
    cfg = Config(os.path.join(root, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(root, "alembic"))
    command.upgrade(cfg, "head")


@pytest.fixture(autouse=True)
def _clean_tables():
    with engine.begin() as conn:
        conn.execute(
            text(
                "TRUNCATE signals, ingestion_jobs, extracted_features, risk_scores "
                "RESTART IDENTITY CASCADE"
            )
        )
    yield


@pytest.fixture(autouse=True)
def _fast_retry(monkeypatch):
    monkeypatch.setenv("RETRY_DELAY_SECONDS", "0")


@pytest.fixture
def sample_window() -> tuple[datetime, datetime]:
    return (
        datetime(2026, 8, 23, tzinfo=timezone.utc),
        datetime(2026, 8, 24, tzinfo=timezone.utc),
    )


@pytest.fixture
def make_signal():
    def _make(
        source: str,
        payload: dict,
        corridor: str = "hormuz",
        commodity: str = "crude_oil",
        fetched_at: datetime | None = None,
    ) -> int:
        with SessionLocal() as session:
            signal = Signal(
                source=source,
                raw_payload=payload,
                corridor=corridor,
                commodity=commodity,
                fetched_at=fetched_at or datetime(2026, 8, 23, 12, tzinfo=timezone.utc),
            )
            session.add(signal)
            session.commit()
            return signal.id

    return _make
