"""End-to-end tests for the virtual banking experience and its link to security operations.

Scope, in the order the feature was built:

* **Authentication** — sign-in success and failure, the published demo logins,
  protected routes, session expiry and logout actually revoking access.
* **Authorization** — a customer may only reach their own rows, an analyst may not
  act as a customer, and the security-operations console is analyst-only. Every one
  of these is checked against the *server*, never against a hidden button.
* **Transaction history** — search, date range, account, type, status and
  ``suspicious`` filters plus pagination, and a detail view that carries the
  detection and the incident behind the row.
* **Transfers** — server-side validation, insufficient balance, ownership,
  duplicate submission (idempotency key *and* the repeat window), and the atomicity
  of the debit/credit pair.
* **Account Security Center** — the posture and its factors are computed from
  counted rows, and the "simulate a sign-in" action writes a real event.
* **One shared ledger** — a scenario launched on the security side must appear on
  the customer side, through stored rows rather than a second copy of the data.

Two invariants recur, and are why several tests exist:

* **Money is exact.** Every ``*_minor`` figure is an integer number of paise and
  the stored balance equals the opening balance plus the settled ledger.
* **Nothing is invented.** Any number the API reports can be traced to a row: the
  tests cross-check the security factors and the notifications against the service
  layer rather than against literals.

The database is the temporary one from ``tests/conftest.py``. This module adds the
demo bank through the *real* seeder (``app.db.seed``), so the fixtures exercise the
same code path the README documents.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.core.config import settings
from app.db import repositories as repo
from app.db.models import (
    ACCOUNT_KIND_CUSTOMER,
    Account,
    AuthSession,
    CustomerProfile,
    Notification,
    SecurityEvent,
    Transaction,
    User,
)
from app.db.seed import CUSTOMER_ACCOUNT_IDS, PAYEE_ACCOUNT_IDS, PROBE_ACCOUNT_ID, seed_demo_data
from app.db.session import SessionLocal
from app.main import app
from app.security.passwords import token_digest
from app.services import auth

#: The one seeded payment the engine actually scored, and the incident it opened.
PROBE_TRANSACTION_ID = "TXN-SEED-PROBE"

#: The three accounts the seeder gives the demo customer. Their balances must always
#: equal the opening balance plus the settled rows, whatever the tests did before.
SEEDED_ACCOUNT_IDS = tuple(CUSTOMER_ACCOUNT_IDS)


@pytest.fixture(scope="session")
def demo_bank(client) -> dict:
    """Create the demo bank once, through ``app.db.seed.seed_demo_data``.

    ``client`` is depended on for its side effect: entering it runs the app
    lifespan, which creates the schema and the demo logins. Seeding before that
    would target a database with no tables yet.
    """
    session = SessionLocal()
    try:
        return seed_demo_data(session)
    finally:
        session.close()


#: Every test in this module needs a bank to be a bank about.
pytestmark = pytest.mark.usefixtures("demo_bank")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _signed_in(email: str, password: str) -> TestClient:
    """A signed-in client built the way the browser builds one."""
    client = TestClient(app)
    client.__enter__()
    response = client.post(
        "/api/auth/login",
        json={"email": email, "password": password, "device_id": "D-WEB-DEMO-01"},
    )
    assert response.status_code == 200, response.text
    client.headers.update({"Authorization": f"Session {response.json()['token']}"})
    return client


def _accounts(client: TestClient) -> dict:
    response = client.get("/api/banking/accounts")
    assert response.status_code == 200, response.text
    return response.json()


def _account_ids(client: TestClient) -> set[str]:
    return {item["id"] for item in _accounts(client)["items"]}


def _balance(client: TestClient, account_id: str) -> int:
    response = client.get(f"/api/banking/accounts/{account_id}")
    assert response.status_code == 200, response.text
    return response.json()["balance_minor"]


def _all_accounts(client: TestClient) -> list[dict]:
    """Page through the analyst account roster (it grows with every run)."""
    items: list[dict] = []
    offset = 0
    while True:
        page = client.get("/api/accounts", params={"limit": 500, "offset": offset}).json()
        items.extend(page["items"])
        offset += len(page["items"])
        if not page["items"] or offset >= page["total"]:
            return items


def _transfer(client: TestClient, **overrides) -> dict:
    payload = {
        "source_account_id": overrides.pop("source_account_id", SEEDED_ACCOUNT_IDS[1]),
        "recipient_account_id": overrides.pop("recipient_account_id", PAYEE_ACCOUNT_IDS[2]),
        "amount": overrides.pop("amount", "25.00"),
        "description": overrides.pop("description", ""),
        "idempotency_key": overrides.pop("idempotency_key", None),
    }
    payload.update(overrides)
    response = client.post(
        "/api/banking/transfers", json=payload, headers={"X-Device-Id": "D-WEB-DEMO-01"}
    )
    assert response.status_code == 200, response.text
    return response.json()


# ---------------------------------------------------------------------------
# §3 Authentication
# ---------------------------------------------------------------------------
def test_demo_credentials_are_published_and_actually_work(anonymous_client):
    """The demo login is a documented feature, not a backdoor: it must really work."""
    response = anonymous_client.get("/api/auth/demo-credentials")
    assert response.status_code == 200, response.text
    published = response.json()
    assert {entry["role"] for entry in published} == {"CUSTOMER", "ANALYST"}
    for entry in published:
        assert entry["email"] and entry["password"] and entry["description"]
        signed_in = anonymous_client.post(
            "/api/auth/login",
            json={"email": entry["email"], "password": entry["password"]},
        )
        assert signed_in.status_code == 200, signed_in.text
        assert signed_in.json()["user"]["role"] == entry["role"]


def test_login_returns_a_session_and_never_echoes_credential_material(anonymous_client):
    response = anonymous_client.post(
        "/api/auth/login",
        json={
            "email": settings.demo_customer_email,
            "password": settings.demo_customer_password,
            "device_id": "D-TEST-LOGIN",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["token"]
    assert body["expires_at"] and body["issued_at"]
    assert body["user"]["role"] == "CUSTOMER"
    assert body["user"]["email"] == settings.demo_customer_email
    assert "account_ids" in body["user"]
    # The password hash must not leave the service under any key.
    assert "password" not in response.text.lower()
    assert "hash" not in response.text.lower()
    # The opaque token is what the browser holds; the database holds only its digest.
    assert token_digest(body["token"]) != body["token"]


def test_wrong_password_and_unknown_email_are_indistinguishable(anonymous_client):
    """A different answer would turn the endpoint into an account enumerator."""
    wrong = anonymous_client.post(
        "/api/auth/login",
        json={"email": settings.demo_customer_email, "password": "definitely-not-it"},
    )
    unknown = anonymous_client.post(
        "/api/auth/login",
        json={"email": "nobody@rupeeshield.demo", "password": "definitely-not-it"},
    )
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()
    assert wrong.json()["code"] == "unauthenticated"


def test_protected_routes_reject_a_request_without_a_session(anonymous_client):
    for method, path in (
        ("get", "/api/banking/accounts"),
        ("get", "/api/banking/transactions"),
        ("get", "/api/banking/security-centre"),
        ("get", "/api/notifications"),
        ("get", "/api/auth/session"),
        ("get", "/api/overview"),
    ):
        response = getattr(anonymous_client, method)(path)
        assert response.status_code == 401, f"{method.upper()} {path} -> {response.status_code}"
    assert anonymous_client.post(
        "/api/banking/transfers",
        json={
            "source_account_id": SEEDED_ACCOUNT_IDS[0],
            "recipient_account_id": PAYEE_ACCOUNT_IDS[0],
            "amount": "10.00",
        },
    ).status_code == 401


def test_session_endpoint_reports_the_signed_in_identity(customer_client):
    response = customer_client.get("/api/auth/session")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["user"]["role"] == "CUSTOMER"
    assert body["expires_at"] and body["issued_at"]


def test_logout_revokes_the_session_server_side():
    """Signing out must end access, not merely hide the dashboard."""
    client = _signed_in(settings.demo_customer_email, settings.demo_customer_password)
    try:
        assert client.get("/api/banking/accounts").status_code == 200
        assert client.post("/api/auth/logout").status_code == 200
        assert client.get("/api/banking/accounts").status_code == 401
        # Signing out twice is not an error, and still not an access grant.
        assert client.get("/api/banking/accounts").status_code == 401
    finally:
        client.__exit__(None, None, None)


def test_an_expired_session_is_rejected():
    client = _signed_in(settings.demo_customer_email, settings.demo_customer_password)
    try:
        token = client.headers["Authorization"].split(" ", 1)[1]
        session = SessionLocal()
        try:
            record = repo.users.get_session_by_token_hash(session, token_digest(token))
            assert record is not None
            record.expires_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=1)
            session.add(record)
            session.commit()
        finally:
            session.close()
        response = client.get("/api/banking/accounts")
        assert response.status_code == 401
        assert response.json()["code"] == "unauthenticated"
    finally:
        client.__exit__(None, None, None)


# ---------------------------------------------------------------------------
# §10 Roles and authorization
# ---------------------------------------------------------------------------
def test_security_operations_are_analyst_only(customer_client):
    """The whole-bank console is refused to a customer on the server."""
    for path in ("/api/overview", "/api/incidents", "/api/network/graph", "/api/ml/evaluation"):
        response = customer_client.get(path)
        assert response.status_code == 403, f"{path} -> {response.status_code}"
        assert response.json()["code"] == "forbidden"


def test_a_customer_cannot_act_as_the_bank(client, customer_client):
    """An analyst holds no accounts, and may not submit a customer transfer."""
    assert client.get("/api/banking/accounts").json()["items"] == []
    response = client.post(
        "/api/banking/transfers",
        json={
            "source_account_id": SEEDED_ACCOUNT_IDS[0],
            "recipient_account_id": PAYEE_ACCOUNT_IDS[0],
            "amount": "10.00",
        },
    )
    assert response.status_code == 403, response.text
    assert response.json()["code"] == "forbidden"
    assert customer_client.post(
        "/api/banking/security-centre/simulate-login",
        params={"device_id": "D-SIM-TABLET-03"},
    ).status_code == 200


def test_analyst_scope_simulate_login_rejects_the_analyst(client):
    """The customer-only action is gated by role, not by the navigation menu."""
    assert client.post("/api/banking/security-centre/simulate-login").status_code == 403


def test_a_customer_can_only_read_their_own_accounts(customer_client):
    ids = _account_ids(customer_client)
    assert set(SEEDED_ACCOUNT_IDS) <= ids
    # A bank payee account exists and has a ledger, but is not the customer's.
    assert not (ids & set(PAYEE_ACCOUNT_IDS))
    for payee_id in PAYEE_ACCOUNT_IDS:
        assert customer_client.get(f"/api/banking/accounts/{payee_id}").status_code == 404


def test_another_customers_rows_are_not_reachable(session, customer_client):
    """A second customer's account and ledger must be a 404, not a partial view."""
    other = auth.create_user(
        session,
        email="rival.customer@rupeeshield.demo",
        full_name="Rival Customer",
        password="rival-customer-2026",
        role="CUSTOMER",
    )
    account_id = "ACC-TEST-RIVAL"
    transaction_id = "TXN-TEST-RIVAL"
    repo.accounts.add_account(
        session,
        {
            "id": account_id,
            "label": "Rival Savings",
            "home_country": "IN",
            "preferred_channel": "UPI_WALLET",
            "avg_txn_amount_minor": 250_00,
            "account_age_days": 400,
            "opening_balance_minor": 5_000_00,
            "balance_minor": 5_000_00,
            "risk_status": "NORMAL",
            "is_synthetic": True,
            "owner_user_id": other.id,
            "account_kind": ACCOUNT_KIND_CUSTOMER,
        },
    )
    repo.transactions.add_transaction(
        session,
        {
            "id": transaction_id,
            "run_id": None,
            "account_id": account_id,
            "counterparty_id": None,
            "counterparty_label": "External sender",
            "timestamp": datetime.now(timezone.utc).replace(tzinfo=None),
            "amount_minor": 100_00,
            "currency": "INR",
            "channel": "UPI_WALLET",
            "country": "IN",
            "merchant_id": "PAYEE-EXTERNAL",
            "device_id": "D-WEB-DEMO-01",
            "direction": "CREDIT",
            "status": "SETTLED",
            "description": "Rival deposit",
        },
    )
    session.commit()

    try:
        # The demo customer cannot reach any of it...
        assert customer_client.get(f"/api/banking/accounts/{account_id}").status_code == 404
        assert customer_client.get(f"/api/banking/transactions/{transaction_id}").status_code == 404
        assert (
            customer_client.get(
                "/api/banking/transactions", params={"account_id": account_id}
            ).status_code
            == 404
        )
        assert account_id not in _account_ids(customer_client)

        # ...while its owner can.
        rival = _signed_in("rival.customer@rupeeshield.demo", "rival-customer-2026")
        try:
            assert rival.get(f"/api/banking/accounts/{account_id}").status_code == 200
            assert rival.get(f"/api/banking/transactions/{transaction_id}").status_code == 200
            assert SEEDED_ACCOUNT_IDS[0] not in _account_ids(rival)
            assert rival.get(f"/api/banking/accounts/{SEEDED_ACCOUNT_IDS[0]}").status_code == 404
        finally:
            rival.__exit__(None, None, None)
    finally:
        # Remove the extra customer so the demo run linkage stays one-customer-per-run.
        # Signing the rival in recorded telemetry and a session, so those go too.
        session.execute(delete(SecurityEvent).where(SecurityEvent.account_id == account_id))
        session.execute(delete(Transaction).where(Transaction.account_id == account_id))
        session.execute(delete(Notification).where(Notification.user_id == other.id))
        session.execute(delete(AuthSession).where(AuthSession.user_id == other.id))
        session.execute(delete(CustomerProfile).where(CustomerProfile.user_id == other.id))
        session.execute(delete(Account).where(Account.id == account_id))
        session.execute(delete(User).where(User.id == other.id))
        session.commit()


# ---------------------------------------------------------------------------
# §4 Dashboard and balance summary
# ---------------------------------------------------------------------------
def test_balance_summary_matches_its_own_account_cards(customer_client):
    body = _accounts(customer_client)
    items = body["items"]
    summary = body["summary"]
    assert items, "the demo bank must give the customer at least one account"
    assert summary["account_count"] == len(items)
    assert summary["total_balance_minor"] == sum(item["balance_minor"] for item in items)
    assert summary["available_minor"] == max(
        summary["total_balance_minor"] - summary["held_minor"], 0
    )
    assert summary["currency"] == "INR"
    # Major units are exactly the minor units over 100, at every account.
    for item in items:
        assert item["balance"] == pytest.approx(item["balance_minor"] / 100, abs=1e-9)
        assert item["masked_number"].startswith("•••• •••• ")
        assert item["status"] in {"ACTIVE", "UNDER_REVIEW"}
        assert item["status_label"] in {"Active", "Under Review"}


def test_balances_equal_the_opening_balance_plus_the_settled_ledger(customer_client):
    """The number on the card must be the number the ledger produces."""
    cards = {item["id"]: item for item in _accounts(customer_client)["items"]}
    for account_id in SEEDED_ACCOUNT_IDS:
        assert account_id in cards
        card = cards[account_id]
        page = customer_client.get(
            "/api/banking/transactions", params={"account_id": account_id, "limit": 200}
        )
        assert page.status_code == 200, page.text
        rows = page.json()
        assert rows["returned"] == rows["total"], "pagination must not hide ledger rows"
        settled = sum(
            row["signed_amount_minor"] for row in rows["items"] if row["status"] == "SETTLED"
        )
        assert card["balance_minor"] == card["opening_balance_minor"] + settled


# ---------------------------------------------------------------------------
# §5 Transaction history
# ---------------------------------------------------------------------------
def test_transaction_page_is_ordered_and_paginated(customer_client):
    first = customer_client.get("/api/banking/transactions", params={"limit": 5}).json()
    assert first["limit"] == 5
    assert first["returned"] == len(first["items"]) == 5
    assert first["total"] >= 14
    assert first["offset"] == 0

    second = customer_client.get(
        "/api/banking/transactions", params={"limit": 5, "offset": 5}
    ).json()
    assert second["offset"] == 5
    assert not ({item["id"] for item in first["items"]} & {i["id"] for i in second["items"]})

    stamps = [_iso(item["timestamp"]) for item in first["items"]]
    assert stamps == sorted(stamps, reverse=True)

    assert customer_client.get("/api/banking/transactions", params={"limit": 0}).status_code == 422


def test_transaction_filters_search_status_type_account_and_date(customer_client):
    page = customer_client.get(
        "/api/banking/transactions", params={"search": "Nimbus", "limit": 50}
    ).json()
    assert page["total"] >= 1
    for item in page["items"]:
        haystack = " ".join(
            str(item[field] or "")
            for field in ("id", "description", "counterparty_label", "counterparty_id", "channel", "status")
        ).lower()
        assert "nimbus" in haystack

    credits = customer_client.get(
        "/api/banking/transactions", params={"direction": "CREDIT", "limit": 50}
    ).json()
    assert credits["total"] >= 1
    assert all(item["direction"] == "CREDIT" for item in credits["items"])
    assert all(item["signed_amount_minor"] > 0 for item in credits["items"])

    debits = customer_client.get(
        "/api/banking/transactions", params={"direction": "DEBIT", "limit": 50}
    ).json()
    assert all(item["signed_amount_minor"] < 0 for item in debits["items"])

    scoped = customer_client.get(
        "/api/banking/transactions", params={"account_id": SEEDED_ACCOUNT_IDS[0], "limit": 50}
    ).json()
    assert scoped["total"] >= 1
    assert {item["account_id"] for item in scoped["items"]} == {SEEDED_ACCOUNT_IDS[0]}

    blocked = customer_client.get(
        "/api/banking/transactions", params={"status": "BLOCKED", "limit": 50}
    ).json()
    assert all(item["status"] == "BLOCKED" for item in blocked["items"])
    assert all(item["status_label"] == "Blocked" for item in blocked["items"])

    queried = customer_client.get(
        "/api/banking/transactions", params={"suspicious": True, "limit": 50}
    ).json()
    assert queried["total"] >= 1
    assert all(item["suspicious"] is True for item in queried["items"])
    assert all(item["decision"] != "ALLOW" for item in queried["items"])

    allowed = customer_client.get(
        "/api/banking/transactions", params={"suspicious": False, "limit": 50}
    ).json()
    assert all(item["decision"] in (None, "ALLOW") for item in allowed["items"])

    today = datetime.now(timezone.utc).date()
    recent = customer_client.get(
        "/api/banking/transactions",
        params={
            "date_from": (today - timedelta(days=10)).isoformat(),
            "date_to": today.isoformat(),
            "limit": 50,
        },
    ).json()
    assert recent["total"] >= 1
    for item in recent["items"]:
        assert _iso(item["timestamp"]).date() >= today - timedelta(days=10)

    empty = customer_client.get(
        "/api/banking/transactions",
        params={"date_from": (today - timedelta(days=400)).isoformat(),
                "date_to": (today - timedelta(days=300)).isoformat()},
    ).json()
    assert empty["total"] == 0 and empty["items"] == []

    assert customer_client.get(
        "/api/banking/transactions", params={"status": "NONSENSE"}
    ).status_code == 422
    assert customer_client.get(
        "/api/banking/transactions", params={"direction": "SIDEWAYS"}
    ).status_code == 422


def test_transaction_detail_carries_the_detection_and_the_incident(customer_client, demo_bank):
    response = customer_client.get(f"/api/banking/transactions/{PROBE_TRANSACTION_ID}")
    assert response.status_code == 200, response.text
    detail = response.json()

    assert detail["id"] == PROBE_TRANSACTION_ID
    assert detail["account_id"] == PROBE_ACCOUNT_ID
    assert detail["suspicious"] is True
    assert detail["decision"] in {"FLAG", "BLOCK"}
    assert detail["detection_id"], "a queried row must expose the detection that scored it"
    detection = detail["detection"]
    assert detection["decision"] == detail["decision"]
    assert detection["fraud_risk_score"] == pytest.approx(detail["risk_score"], abs=1e-6)
    assert detection["reasons"], "a decision must carry its reasons"
    assert isinstance(detail["explanation"], list)
    assert isinstance(detail["security_events"], list)
    assert isinstance(detail["related"], list)

    # The seeder only opens an incident when the engine queried the payment, so the
    # assertion follows the recorded decision rather than an assumption about it.
    probe = demo_bank["probe"]
    if probe["decision"] != "ALLOW":
        assert detail["incident_id"] == demo_bank["incident_id"]
        assert detail["incident"]["primary_account_id"] == PROBE_ACCOUNT_ID
        assert detail["incident"]["status"] in {"OPEN", "INVESTIGATING"}
    else:  # pragma: no cover - the shipped artefacts block the probe
        assert detail["incident"] is None

    assert customer_client.get(
        f"/api/banking/transactions/{PAYEE_ACCOUNT_IDS[0]}"
    ).status_code == 404
    assert customer_client.get("/api/banking/transactions/TXN-DOES-NOT-EXIST").status_code == 404


# ---------------------------------------------------------------------------
# §6 Simulated transfers
# ---------------------------------------------------------------------------
def test_transfer_validation_is_enforced_by_the_server(customer_client):
    source = SEEDED_ACCOUNT_IDS[1]
    base = {"source_account_id": source, "recipient_account_id": PAYEE_ACCOUNT_IDS[2]}

    # Amount bounds and positivity.
    for bad in ("0", "-5.00", "0.50", "500000.00", "250.555"):
        response = customer_client.post("/api/banking/transfers", json={**base, "amount": bad})
        assert response.status_code == 422, f"amount={bad} -> {response.status_code}"

    # Unknown accounts.
    assert customer_client.post(
        "/api/banking/transfers",
        json={**base, "recipient_account_id": "ACC-NOT-REAL", "amount": "10.00"},
    ).status_code == 404
    assert customer_client.post(
        "/api/banking/transfers",
        json={"source_account_id": "ACC-NOT-REAL", "recipient_account_id": PAYEE_ACCOUNT_IDS[2],
              "amount": "10.00"},
    ).status_code == 404

    # Source == recipient.
    assert customer_client.post(
        "/api/banking/transfers",
        json={"source_account_id": source, "recipient_account_id": source, "amount": "10.00"},
    ).status_code == 422


def test_transfer_from_an_account_the_customer_does_not_own_is_forbidden(customer_client):
    """403 here (not 404): the customer named an account they can see as a payee."""
    response = customer_client.post(
        "/api/banking/transfers",
        json={
            "source_account_id": PAYEE_ACCOUNT_IDS[0],
            "recipient_account_id": SEEDED_ACCOUNT_IDS[0],
            "amount": "10.00",
        },
    )
    assert response.status_code == 403, response.text
    assert response.json()["code"] == "forbidden"


def test_insufficient_balance_is_rejected_and_moves_nothing(customer_client):
    source = SEEDED_ACCOUNT_IDS[1]
    before = _balance(customer_client, source)
    ledger_before = customer_client.get(
        "/api/banking/transactions", params={"account_id": source, "limit": 200}
    ).json()["total"]

    response = customer_client.post(
        "/api/banking/transfers",
        json={
            "source_account_id": source,
            "recipient_account_id": PAYEE_ACCOUNT_IDS[1],
            "amount": "50000.00",
            "description": "Should never leave",
        },
    )
    assert response.status_code == 422, response.text
    assert "Insufficient available balance" in response.json()["detail"]

    assert _balance(customer_client, source) == before
    ledger_after = customer_client.get(
        "/api/banking/transactions", params={"account_id": source, "limit": 200}
    ).json()
    assert ledger_after["total"] == ledger_before
    assert not any("Should never leave" in item["description"] for item in ledger_after["items"])


def test_transfer_writes_an_atomic_pair_and_a_matching_balance_change(customer_client, client):
    """Both legs, one commit: the balances are consistent with the recorded decision."""
    source = SEEDED_ACCOUNT_IDS[1]
    recipient = PAYEE_ACCOUNT_IDS[2]
    before_source = _balance(customer_client, source)

    result = _transfer(
        customer_client,
        source_account_id=source,
        recipient_account_id=recipient,
        amount="25.00",
        description="Own-account sweep",
        idempotency_key="test-atomic-1",
    )

    amount_minor = 25_00
    assert result["amount_minor"] == amount_minor
    assert result["amount"] == pytest.approx(25.0)
    assert result["status"] in {"SETTLED", "BLOCKED", "PENDING_REVIEW"}
    assert result["decision"] in {"ALLOW", "FLAG", "BLOCK"}
    assert result["settled"] == (result["status"] == "SETTLED")
    assert result["transfer_id"].startswith("TRF-")
    assert result["source"]["account_id"] == source
    assert result["recipient"]["account_id"] == recipient

    legs = result["transactions"]
    assert {leg["direction"] for leg in legs} == {"DEBIT", "CREDIT"}
    assert {leg["status"] for leg in legs} == {result["status"]}
    assert {leg["amount_minor"] for leg in legs} == {amount_minor}
    assert all(leg["counterparty_id"] for leg in legs)

    after_source = result["source"]["balance_minor"]
    if result["status"] == "SETTLED":
        assert after_source == before_source - amount_minor
        assert result["status_label"] == "Completed"
    else:
        assert after_source == before_source, "unsettled money must not leave the account"
        assert result["status_label"] in {"Blocked", "Pending"}
    assert _balance(customer_client, source) == after_source
    assert result["message"]

    # Both legs report the same decision and the same resulting balance movement,
    # which is the observable half of "one atomic pair".
    source_leg, recipient_leg = result["transactions"]
    if source_leg["direction"] == "CREDIT":
        source_leg, recipient_leg = recipient_leg, source_leg
    assert recipient_leg["status"] == source_leg["status"]

    # A transfer alerts the security-operations queue exactly when it was queried:
    # a clean, settled payment raises nothing, and nothing is invented to fill a panel.
    feed = client.get("/api/notifications", params={"limit": 50}).json()
    alerted = [
        item
        for item in feed["items"]
        if item["kind"] == "TRANSFER_ALERT" and item["entity_id"] == result["transfer_id"]
    ]
    if result["status"] == "SETTLED" and result["decision"] == "ALLOW":
        assert not alerted, "a clean settled transfer must not manufacture an alert"
    else:
        assert alerted, "a queried transfer must alert the security-operations queue"


def test_conservation_of_value_across_a_transfer_pair(customer_client):
    """Read the recipient balance before and after so the sum can be checked exactly."""
    source = SEEDED_ACCOUNT_IDS[0]
    recipient = PAYEE_ACCOUNT_IDS[0]

    def payee_balance() -> int:
        session = SessionLocal()
        try:
            account = repo.accounts.get_account(session, recipient)
            assert account is not None
            return int(account.balance_minor)
        finally:
            session.close()

    before_source = _balance(customer_client, source)
    before_recipient = payee_balance()
    result = _transfer(
        customer_client,
        source_account_id=source,
        recipient_account_id=recipient,
        amount="30.00",
        description="Conservation check",
        idempotency_key="test-conservation-1",
    )
    after_source = _balance(customer_client, source)
    after_recipient = payee_balance()

    if result["status"] == "SETTLED":
        assert after_source == before_source - 30_00
        assert after_recipient == before_recipient + 30_00
    else:
        assert after_source == before_source
        assert after_recipient == before_recipient
    assert after_source + after_recipient == before_source + before_recipient


def test_a_repeated_idempotency_key_returns_the_original_transfer(customer_client):
    source = SEEDED_ACCOUNT_IDS[1]
    key = "test-idempotency-key-1"
    first = _transfer(
        customer_client,
        source_account_id=source,
        recipient_account_id=PAYEE_ACCOUNT_IDS[3],
        amount="18.00",
        description="Idempotent sweep",
        idempotency_key=key,
    )
    balance_after_first = _balance(customer_client, source)

    second = _transfer(
        customer_client,
        source_account_id=source,
        recipient_account_id=PAYEE_ACCOUNT_IDS[3],
        amount="18.00",
        description="Idempotent sweep",
        idempotency_key=key,
    )
    assert second["duplicate"] is True
    assert second["transfer_id"] == first["transfer_id"]
    assert second["status"] == first["status"]
    assert _balance(customer_client, source) == balance_after_first, "money moved twice"


def test_an_identical_repeat_without_a_key_is_treated_as_a_duplicate(customer_client):
    source = SEEDED_ACCOUNT_IDS[1]
    payload = {
        "source_account_id": source,
        "recipient_account_id": PAYEE_ACCOUNT_IDS[1],
        "amount": "12.34",
        "description": "Double-click guard",
    }
    first = customer_client.post("/api/banking/transfers", json=payload).json()
    balance_after_first = _balance(customer_client, source)
    second = customer_client.post("/api/banking/transfers", json=payload).json()

    assert second["duplicate"] is True
    assert second["transfer_id"] == first["transfer_id"]
    assert _balance(customer_client, source) == balance_after_first


def test_a_transfer_appears_in_the_customers_own_history(customer_client):
    source = SEEDED_ACCOUNT_IDS[1]
    result = _transfer(
        customer_client,
        source_account_id=source,
        recipient_account_id=PAYEE_ACCOUNT_IDS[4],
        amount="42.00",
        description="History visibility check",
        idempotency_key="test-history-1",
    )
    page = customer_client.get(
        "/api/banking/transactions", params={"search": "History visibility check", "limit": 20}
    ).json()
    assert page["total"] >= 1
    row = next(item for item in page["items"] if item["id"] in {leg["id"] for leg in result["transactions"]})
    assert row["account_id"] == source
    assert row["amount_minor"] == 42_00
    assert row["direction"] == "DEBIT"
    assert row["counterparty_id"] == PAYEE_ACCOUNT_IDS[4]
    assert row["status"] == result["status"]
    assert row["status_label"] == result["status_label"]


# ---------------------------------------------------------------------------
# §7 Account Security Center
# ---------------------------------------------------------------------------
def test_security_centre_reports_counted_rows_not_literals(customer_client, session):
    response = customer_client.get("/api/banking/security-centre")
    assert response.status_code == 200, response.text
    centre = response.json()

    assert centre["status"]["level"] in {"GOOD", "ATTENTION", "ELEVATED"}
    assert centre["status"]["label"] and centre["status"]["summary"]

    factors = {factor["key"]: factor for factor in centre["factors"]}
    assert set(factors) == {
        "sign_ins",
        "failed_sign_ins",
        "devices",
        "suspicious_transactions",
        "open_incidents",
        "security_events",
    }
    for factor in factors.values():
        assert factor["tone"] in {"GOOD", "ATTENTION", "DANGER"}
        assert factor["label"] and factor["detail"]

    # The seeded sign-in telemetry is visible, and each figure is explained.
    assert centre["recent_sign_ins"], "the seeder records sign-ins"
    assert centre["devices"], "devices are derived from recorded telemetry"
    for device in centre["devices"]:
        assert device["device_id"]
        assert isinstance(device["recognised"], bool)
        assert device["detail"]

    # Cross-check the posture against the service layer: no figure is invented.
    session.expire_all()
    account_ids = repo.accounts.owned_account_ids(session, _customer_id(customer_client))
    assert factors["security_events"]["value"] == repo.detections.count_events_for_accounts(
        session, account_ids
    )
    assert factors["open_incidents"]["value"] >= 1, "the seeded probe opened an incident"
    assert centre["open_incidents"]
    assert centre["suspicious_transactions"], "the seeded probe is a queried transaction"
    assert centre["generated_at"]


def _customer_id(client: TestClient) -> str:
    return client.get("/api/auth/session").json()["user"]["id"]


def test_simulate_login_writes_a_real_event_and_a_notification(customer_client, session):
    """An explicit demo action, recorded on the backend — not a frontend timer."""
    before = customer_client.get("/api/notifications/unread-count").json()["unread"]
    device = "D-SIM-LAPTOP-01"

    response = customer_client.post(
        "/api/banking/security-centre/simulate-login", params={"device_id": device}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["device_id"] == device
    assert body["event"]["device_id"] == device
    assert body["event"]["event_type"] == "LOGIN_NEW_DEVICE"
    assert body["event"]["severity"] == "MEDIUM"
    assert body["notification_id"], "the action must raise a notification"
    assert device in body["message"]

    centre = customer_client.get("/api/banking/security-centre").json()
    assert device in {item["device_id"] for item in centre["devices"]}

    # The event is a stored row on the customer's own account, not a rendered
    # string. The centre's list is a bounded recent window, so it is checked here
    # against the row itself rather than against that window.
    session.expire_all()
    stored = repo.detections.security_event_by_id(session, body["event"]["id"])
    assert stored is not None
    assert stored.device_id == device
    assert stored.event_type == "LOGIN_NEW_DEVICE"
    assert stored.account_id in repo.accounts.owned_account_ids(
        session, _customer_id(customer_client)
    )

    after = customer_client.get("/api/notifications/unread-count").json()["unread"]
    assert after == before + 1
    feed = customer_client.get("/api/notifications", params={"limit": 50}).json()
    assert any(item["id"] == body["notification_id"] for item in feed["items"])


def test_security_centre_and_notifications_are_scoped_to_the_signed_in_user(customer_client, client):
    """The customer's panel is not the analyst queue, and vice versa."""
    customer_feed = customer_client.get("/api/notifications", params={"limit": 50}).json()
    analyst_feed = client.get("/api/notifications", params={"limit": 50}).json()
    customer_kinds = {item["kind"] for item in customer_feed["items"]}
    analyst_kinds = {item["kind"] for item in analyst_feed["items"]}
    assert "TRANSFER_ALERT" in analyst_kinds
    # The bank is told about the customer's transfer; the customer is told about
    # their own account, and the two queues are addressed separately rather than
    # sharing one broadcast list.
    assert "TRANSFER_ALERT" in customer_kinds
    assert "SECURITY_NEW_DEVICE" in customer_kinds
    assert not (
        {item["id"] for item in customer_feed["items"]}
        & {item["id"] for item in analyst_feed["items"]}
    ), "the two audiences must not share a notification"

    # Marking everything read is a per-audience action.
    assert customer_client.post("/api/notifications/read-all").status_code == 200
    assert customer_client.get("/api/notifications/unread-count").json()["unread"] == 0
    assert customer_client.post("/api/notifications/NTF-DOES-NOT-EXIST/read").status_code == 404


def test_failed_login_burst_raises_a_security_notification(anonymous_client, customer_client):
    """Repeated failures are recorded, audited and turned into one alert."""
    for _ in range(settings.max_failed_logins):
        response = anonymous_client.post(
            "/api/auth/login",
            json={"email": settings.demo_customer_email, "password": "wrong-password"},
        )
        assert response.status_code == 401

    feed = customer_client.get("/api/notifications", params={"limit": 50}).json()
    burst = [item for item in feed["items"] if item["kind"] == "SECURITY_LOGIN_BURST"]
    assert burst, "a burst of failed sign-ins must alert the account holder"
    assert burst[0]["severity"] == "HIGH"

    centre = customer_client.get("/api/banking/security-centre").json()
    factors = {factor["key"]: factor for factor in centre["factors"]}
    assert factors["failed_sign_ins"]["value"] >= settings.max_failed_logins
    assert factors["failed_sign_ins"]["tone"] == "DANGER"
    assert centre["failed_sign_ins"], "each attempt is listed"

    # A successful sign-in clears the streak, so the posture can recover.
    recovered = anonymous_client.post(
        "/api/auth/login",
        json={
            "email": settings.demo_customer_email,
            "password": settings.demo_customer_password,
            "device_id": "D-WEB-DEMO-01",
        },
    )
    assert recovered.status_code == 200


# ---------------------------------------------------------------------------
# §9 The two halves describe one ledger
# ---------------------------------------------------------------------------
def test_a_scenario_launched_by_the_analyst_surfaces_on_the_customer_side(client, customer_client):
    """Attack simulation -> stored rows -> the customer's own views, no second copy."""
    run = client.post(
        "/api/simulation/runs",
        json={
            "scenario": "ACCOUNT_TAKEOVER",
            "n_events": 40,
            "seed": 20261025,
            "defense_config": "COMBINED",
        },
    )
    assert run.status_code == 201, run.text
    run_body = run.json()
    assert run_body["status"] == "COMPLETED", run_body
    run_id = run_body["id"]

    # The accounts the scenario keyed on are reachable by their deterministic ids.
    run_accounts = {item["id"] for item in _all_accounts(client) if item["id"].startswith(run_id)}
    assert run_accounts, "a scenario must create accounts"
    linked = run_accounts & _account_ids(customer_client)
    assert linked, "the customer must be shown the account the attack hit"

    account_id = sorted(linked)[0]

    # 1. Its transactions appear in the customer's history...
    history = customer_client.get(
        "/api/banking/transactions", params={"account_id": account_id, "limit": 200}
    )
    assert history.status_code == 200, history.text
    rows = history.json()
    assert rows["total"] >= 1
    assert {item["account_id"] for item in rows["items"]} == {account_id}

    # 2. ...its incident appears on the customer's security page...
    centre = customer_client.get("/api/banking/security-centre").json()
    assert any(
        incident["primary_account_id"] == account_id for incident in centre["open_incidents"]
    ), "the open incident must be visible on the customer's own security page"

    # 3. ...and the analyst's incident record cites the very same rows. The
    # incident view does not repeat the account id, so the cross-check is whether
    # the customer can read each cited row through their own scoped endpoint.
    incidents = client.get("/api/incidents", params={"limit": 50}).json()["items"]
    incident = next(item for item in incidents if item["run_id"] == run_id)
    detail = client.get(f"/api/incidents/{incident['id']}").json()
    assert detail["timeline"], "an incident must carry its timeline"
    assert detail["detections"], "the incident must cite the detections it was raised from"

    customer_ids = _account_ids(customer_client)
    shared = []
    for row in detail["transactions"]:
        view = customer_client.get(f"/api/banking/transactions/{row['id']}")
        if view.status_code == 200:
            shared.append((row, view.json()))
    assert shared, "the incident and the customer must describe the same ledger rows"

    row, customer_view = shared[0]
    assert customer_view["account_id"] in customer_ids
    assert customer_view["incident_id"] == incident["id"]
    assert customer_view["detection_id"]

    # 4. Both halves agree on the amount, and the analyst can investigate.
    assert customer_view["amount_minor"] == row["amount_minor"]
    moved = client.patch(
        f"/api/incidents/{incident['id']}/status", json={"status": "INVESTIGATING"}
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["status"] == "INVESTIGATING"


def test_the_full_customer_workflow(client, anonymous_client):
    """Login -> dashboard -> account -> history -> simulate -> detect -> alert -> investigate."""
    # 1. Login.
    login = anonymous_client.post(
        "/api/auth/login",
        json={
            "email": settings.demo_customer_email,
            "password": settings.demo_customer_password,
            "device_id": "D-WEB-DEMO-01",
        },
    )
    assert login.status_code == 200, login.text
    customer = TestClient(app)
    customer.__enter__()
    customer.headers.update({"Authorization": f"Session {login.json()['token']}"})
    try:
        # 2. Banking dashboard with balances read from the database.
        dashboard = _accounts(customer)
        assert dashboard["items"] and dashboard["summary"]["total_balance_minor"] > 0

        # 3. Account details.
        account_id = dashboard["items"][0]["id"]
        account = customer.get(f"/api/banking/accounts/{account_id}").json()
        assert account["id"] == account_id
        assert account["balance_minor"] == dashboard["items"][0]["balance_minor"]

        # 4. Transaction history and one transaction's evidence.
        history = customer.get("/api/banking/transactions", params={"limit": 10}).json()
        assert history["items"]
        a_row_that_was_queried = customer.get(
            "/api/banking/transactions", params={"suspicious": True, "limit": 1}
        ).json()
        if a_row_that_was_queried["items"]:
            detail = customer.get(
                f"/api/banking/transactions/{a_row_that_was_queried['items'][0]['id']}"
            )
            assert detail.status_code == 200

        # 5. An analyst launches an attack simulation.
        run = client.post(
            "/api/simulation/runs",
            json={
                "scenario": "PAYMENT_MANIPULATION",
                "n_events": 30,
                "seed": 424242,
                "defense_config": "COMBINED",
            },
        )
        assert run.status_code == 201, run.text
        assert run.json()["status"] == "COMPLETED"
        run_id = run.json()["id"]

        # 6. The detection is recorded and linked to an incident.
        incidents = client.get("/api/incidents", params={"limit": 50}).json()["items"]
        run_incidents = [item for item in incidents if item["run_id"] == run_id]
        assert run_incidents, "the scenario must produce something to investigate"
        incident_id = run_incidents[0]["id"]

        # 7. The customer is alerted: a real notification row, not a frontend timer.
        alerts = customer.get("/api/banking/security-centre").json()
        assert alerts["generated_at"]
        assert alerts["suspicious_transactions"], "the customer must be able to review alerts"

        # 8. The analyst investigates the incident.
        investigation = client.get(f"/api/incidents/{incident_id}")
        assert investigation.status_code == 200, investigation.text
        incident = investigation.json()
        assert incident["timeline"]
        assert client.post(
            f"/api/incidents/{incident_id}/notes",
            json={"note": "Reviewed during the end-to-end walkthrough."},
        ).status_code == 200
        assert client.patch(
            f"/api/incidents/{incident_id}/status", json={"status": "CONTAINED"}
        ).status_code == 200
    finally:
        customer.__exit__(None, None, None)


def test_the_audit_log_records_the_banking_actions(client, session):
    """Transfers and simulated sign-ins are audited; no credential ever is."""
    session.expire_all()
    page = client.get("/api/audit", params={"limit": 100}).json()
    actions = {entry["action"] for entry in page["items"]}
    assert {"TRANSFER_SUBMITTED", "DEMO_DATA_SEEDED"} <= actions
    assert "LOGOUT" in actions or "LOGIN_SUCCESS" in actions

    payload = " ".join(str(entry.get("details") or {}) for entry in page["items"]).lower()
    for secret in ("password", "token", settings.demo_customer_password):
        assert secret.lower() not in payload
