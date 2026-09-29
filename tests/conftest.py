"""Shared pytest fixtures."""
from __future__ import annotations

import pytest

from app import config
from app.db import init_db


@pytest.fixture()
def db(tmp_path, monkeypatch):
    """Point the app at a throwaway SQLite file for each test."""
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    init_db()
    yield
