import os
from pathlib import Path

import bcrypt
import pytest

os.environ.setdefault("PANEL_PASS", "correct horse battery staple 00")
os.environ.setdefault("PANEL_USER", "operator")
os.environ.setdefault("PANEL_COOKIE_SECURE", "0")


@pytest.fixture
def tmp_settings(tmp_path: Path):
    from panel.config import Settings

    os.environ["PANEL_DB"] = str(tmp_path / "test.db")
    os.environ["PANEL_SECRET"] = "unit-test-secret"
    os.environ["PANEL_ARTIFACTS"] = str(tmp_path / "artifacts")
    return Settings.load(env_file=str(tmp_path / "missing.env"))


@pytest.fixture
def hashed_password() -> str:
    return bcrypt.hashpw(b"correct horse battery staple 00", bcrypt.gensalt(rounds=4)).decode()
