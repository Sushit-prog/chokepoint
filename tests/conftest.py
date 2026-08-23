import os
from datetime import datetime, timezone

import pytest
from sqlalchemy import text

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg://postgres:postgres@localhost:15432/chokepoint",
)

from alembic import command
from alembic.config import Config

from src.db.session import engine


@pytest.fixture(scope="session", autouse=True)
def _migrated_db():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cfg = Config(os.path.join(root, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(root, "alembic"))
    command.upgrade(cfg, "head")


@pytest.fixture(autouse=True)
def _clean_tables():
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE signals, ingestion_jobs RESTART IDENTITY CASCADE"))
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
