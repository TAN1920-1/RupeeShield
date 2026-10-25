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

from app.core.config import settings  # noqa: E402
from app.db.session import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.security.ratelimit import rate_limiter  # noqa: E402
from app.services.settings_service import reset_overrides  # noqa: E402

#: Scenario used by the shared fixtures. ACCOUNT_TAKEOVER produces both security
#: telemetry and transactions from more than one account, so the same run exercises
#: the incident queue, the graph and the correlation rules.
SHARED_SCENARIO = "ACCOUNT_TAKEOVER"
SHARED_SEED = 4242
SHARED_EVENTS = 60


def _sign_in(test_client: TestClient, email: str, password: str) -> str:
    """Authenticate against the real endpoint and return the opaque session token."""
    response = test_client.post(
        "/api/auth/login",
        json={"email": email, "password": password, "device_id": "D-TEST-RUNNER"},
    )
    assert response.status_code == 200, response.text
    return response.json()["token"]


def _authenticated_client(email: str, password: str) -> TestClient:
    """A ``TestClient`` holding a freshly issued session for ``email``."""
    test_client = TestClient(app)
    test_client.__enter__()
    test_client.headers.update(
        {"Authorization": f"Session {_sign_in(test_client, email, password)}"}
    )
    return test_client


@pytest.fixture(scope="session")
def client():
    """A ``TestClient`` signed in as the demo **analyst**.

    The security-operations endpoints are ANALYST-only, and that check happens on the
    server, so the HTTP suite authenticates for real rather than being handed a
    bypass. Tests that need no session or a customer session ask for
    ``anonymous_client`` / ``customer_client`` instead.
    """
    with TestClient(app) as test_client:
        token = _sign_in(
            test_client, settings.demo_analyst_email, settings.demo_analyst_password
        )
        test_client.headers.update({"Authorization": f"Session {token}"})
        yield test_client


@pytest.fixture(scope="session")
def customer_client():
    """A ``TestClient`` signed in as the demo **customer** (Priya Sharma)."""
    test_client = _authenticated_client(
        settings.demo_customer_email, settings.demo_customer_password
    )
    yield test_client
    test_client.__exit__(None, None, None)


@pytest.fixture(scope="session")
def anonymous_client():
    """A ``TestClient`` with no session at all, for the 401 / sign-in tests."""
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


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """Clear the process-wide sliding window so the suite is order-independent."""
    rate_limiter.reset()
    yield
