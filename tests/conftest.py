import os

import pytest

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg://postgres:postgres@localhost:15432/chokepoint",
)

from alembic import command
from alembic.config import Config


@pytest.fixture(scope="session", autouse=True)
def _migrated_db():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cfg = Config(os.path.join(root, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(root, "alembic"))
    command.upgrade(cfg, "head")
