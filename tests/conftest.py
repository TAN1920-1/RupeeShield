"""Shared pytest fixtures.

The database is redirected to a temporary SQLite file **before** anything under
``app`` is imported. That ordering matters: ``app.core.config.settings`` is a
module-level singleton and ``app.db.session`` builds its engine at import time, so
setting ``RUPESHIELD_DATABASE_URL`` later would silently test against the developer's
real ``rupeeshield.db``.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

_TMP_DIR = Path(tempfile.mkdtemp(prefix="rupeeshield-tests-"))
os.environ["RUPESHIELD_DATABASE_URL"] = f"sqlite:///{(_TMP_DIR / 'rupeeshield-test.db').as_posix()}"
# Threshold overrides are persisted to ``<data_dir>/thresholds.json``. Pointing the
# data directory at the temporary folder keeps a test run from touching the
# developer's own overlay (and from deleting it).
os.environ["RUPESHIELD_DATA_DIR"] = str(_TMP_DIR)
os.environ.setdefault("RUPESHIELD_LOG_LEVEL", "WARNING")
os.environ.setdefault("RUPESHIELD_ENVIRONMENT", "test")

import pytest  # noqa: E402  (must follow the environment setup above)
from fastapi.testclient import TestClient  # noqa: E402

from app.db.session import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.services.settings_service import reset_overrides  # noqa: E402

#: Scenario used by the shared fixtures. ACCOUNT_TAKEOVER produces both security
#: telemetry and transactions from more than one account, so the same run exercises
#: the incident queue, the graph and the correlation rules.
SHARED_SCENARIO = "ACCOUNT_TAKEOVER"
SHARED_SEED = 4242
SHARED_EVENTS = 60


@pytest.fixture(scope="session")
def client():
    """A ``TestClient`` bound to the app, with startup/shutdown lifespan events."""
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def session():
    """A direct database session for service-level tests."""
    init_db()
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture(scope="session")
def completed_run(client) -> dict:
    """One finished simulation run, reused by the read-only API tests.

    Creating a run is the expensive part of the suite (the whole scenario is
    replayed through the real scorer), so it happens once per session instead of
    once per test.
    """
    response = client.post(
        "/api/simulation/runs",
        json={
            "scenario": SHARED_SCENARIO,
            "n_events": SHARED_EVENTS,
            "seed": SHARED_SEED,
            "defense_config": "COMBINED",
        },
    )
    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["status"] == "COMPLETED", payload
    return payload


@pytest.fixture(autouse=True)
def _restore_thresholds():
    """Undo any settings mutation and remove the persisted overlay after each test."""
    yield
    reset_overrides()
