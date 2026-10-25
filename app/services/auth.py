"""Local authentication service.

Design
------
* **Passwords** are PBKDF2-HMAC-SHA256 with a per-password salt (see
  :mod:`app.security.passwords`). A plaintext password is never stored, logged,
  audited or returned.
* **Sessions** are server-side rows. The browser holds an opaque random token;
  the database holds only its SHA-256 digest. Expiry and revocation are checked on
  every request, so signing out genuinely ends access instead of merely hiding a
  button.
* **Sign-in telemetry is real**: every attempt writes a ``LOGIN_SUCCESS`` /
  ``LOGIN_FAILED`` security event against the account it concerns, which is why a
  failed-login burst can be picked up by the correlation rules and appear on the
  customer's security page.
* **Failures are indistinguishable**: a wrong password and an unknown email return
  the same message, so the endpoint cannot be used to enumerate accounts.

This is an educational local login, not an identity provider. There is no
federation, no SSO and no production secret: the demo credentials are published in
the README and live in configuration.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.db import repositories as repo
from app.db.models import ROLE_ANALYST, ROLE_CUSTOMER, Account, User
from app.security.access import AuthenticationRequired
from app.security.audit import record_audit
from app.security.passwords import (
    INVALID_CREDENTIALS,
    hash_password,
    new_token,
    token_digest,
    verify_password,
)
from app.security.validation import ValidationError, sanitise_text
from app.services import event_types as events
from app.services import notifications
from app.utils.ids import new_id
from app.utils.timeutils import add_minutes, iso_utc, utcnow

logger = get_logger(__name__)

DEMO_CUSTOMER_ID = "USR-DEMO-CUST"
DEMO_ANALYST_ID = "USR-DEMO-ANALYST"

#: Device fingerprint sent by the browser. Bounded and sanitised at the boundary.
DEFAULT_DEVICE = "D-WEB-UNKNOWN"


# ---------------------------------------------------------------------------
# Shaping
# ---------------------------------------------------------------------------
def primary_account_id(session: Session, user: User) -> str | None:
    """The account a login event is attributed to (first of the customer's accounts)."""
    accounts = repo.accounts.accounts_for_owner(session, user.id)
    return accounts[0].id if accounts else None


def shape_user(session: Session, user: User) -> dict:
    """The payload the frontend stores after login. Contains no credential material."""
    profile = repo.users.profile_for(session, user.id)
    accounts = repo.accounts.accounts_for_owner(session, user.id)
    return {
        "id": user.id,
        "email": user.email,
        "full_name": user.full_name,
        "role": user.role,
        "is_demo": bool(user.is_demo),
        "last_login_at": iso_utc(user.last_login_at) if user.last_login_at else None,
        "profile": {
            "phone_masked": profile.phone_masked if profile else "",
            "city": profile.city if profile else "",
            "kyc_status": profile.kyc_status if profile else "VERIFIED",
        },
        "account_ids": [account.id for account in accounts],
        "account_count": len(accounts),
    }


def shape_session(session: Session, user: User, record, token: str) -> dict:
    return {
        "token": token,
        "expires_at": iso_utc(record.expires_at),
        "issued_at": iso_utc(record.issued_at),
        "user": shape_user(session, user),
    }


# ---------------------------------------------------------------------------
# Sign-in / sign-out
# ---------------------------------------------------------------------------
def record_login_event(
    session: Session,
    user: User,
    *,
    event_type: str,
    severity: str,
    device_id: str,
    country: str | None,
    details: dict | None = None,
) -> str | None:
    """Write one authentication security event against the user's own account."""
    account_id = primary_account_id(session, user)
    if account_id is None:
        return None
    event_id = new_id("SEC")
    repo.detections.add_security_event(
        session,
        {
            "id": event_id,
            "run_id": None,
            "account_id": account_id,
            "timestamp": utcnow(),
            "event_type": event_type,
            "severity": severity,
            "country": country,
            "device_id": device_id,
            # The browser is never allowed to hand us a real address.
            "source_ip_hash": new_id("IPH", 16),
            "details": {"simulated": True, "channel": "web", **(details or {})},
        },
    )
    return event_id


def _known_devices(session: Session, account_ids: list[str]) -> set[str]:
    return {
        device
        for device, _country, _seen in repo.detections.known_devices(session, account_ids)
        if device
    }


def authenticate(
    session: Session,
    *,
    email: str,
    password: str,
    device_id: str = DEFAULT_DEVICE,
    country: str | None = None,
    client_host: str = "",
    user_agent: str = "",
) -> tuple[User, dict]:
    """Verify credentials, issue a session and record the sign-in telemetry.

    Raises :class:`AuthenticationRequired` for every failure mode with the same
    message, so the endpoint reveals nothing about which accounts exist.
    """
    candidate = (email or "").strip()
    if not candidate or not password:
        raise AuthenticationRequired(INVALID_CREDENTIALS)

    user = repo.users.get_user_by_email(session, candidate)
    if user is None:
        # Still audited: an attempt against an unknown account is worth seeing.
        record_audit(
            session,
            action="LOGIN_FAILED",
            actor=candidate[:60],
            entity_type="user",
            outcome="FAILED",
            details={"reason": "unknown_account"},
        )
        raise AuthenticationRequired(INVALID_CREDENTIALS)

    device = (device_id or DEFAULT_DEVICE).strip()[:40] or DEFAULT_DEVICE

    if not user.is_active:
        record_audit(
            session,
            action="LOGIN_FAILED",
            actor=user.email,
            entity_type="user",
            entity_id=user.id,
            outcome="FAILED",
            details={"reason": "inactive"},
        )
        raise AuthenticationRequired("This account is not active. Contact an administrator.")

    if not verify_password(password, user.password_hash):
        failures = repo.users.register_login_failure(session, user)
        record_login_event(
            session,
            user,
            event_type=events.FAILED_LOGIN,
            severity="MEDIUM" if failures < settings.max_failed_logins else "HIGH",
            device_id=device,
            country=country,
            details={"consecutive_failures": failures},
        )
        record_audit(
            session,
            action="LOGIN_FAILED",
            actor=user.email,
            entity_type="user",
            entity_id=user.id,
            outcome="FAILED",
            details={"consecutive_failures": failures},
        )
        if failures >= settings.max_failed_logins:
            notifications.notify(
                session,
                user_id=user.id,
                kind="SECURITY_LOGIN_BURST",
                severity="HIGH",
                title=f"{failures} consecutive failed sign-in attempts",
                body=(
                    "Repeated failures were recorded against your account. If this was not "
                    "you, review your recent sign-in activity."
                ),
                entity_type="user",
                entity_id=user.id,
                dedupe_key=f"login-burst:{user.id}:{failures}",
            )
        session.commit()
        raise AuthenticationRequired(INVALID_CREDENTIALS)

    # -- success -----------------------------------------------------------
    account_ids = repo.accounts.owned_account_ids(session, user.id)
    known = _known_devices(session, account_ids)
    is_new_device = bool(account_ids) and device not in known

    repo.users.register_login_success(session, user, utcnow())
    record_login_event(
        session,
        user,
        event_type=events.LOGIN_NEW_DEVICE if is_new_device else events.LOGIN_SUCCESS,
        severity="MEDIUM" if is_new_device else "INFO",
        device_id=device,
        country=country,
        details={"new_device": is_new_device, "user_agent": (user_agent or "")[:120]},
    )
    record, token = issue_session(
        session, user, client_host=client_host, user_agent=user_agent
    )
    session.commit()

    record_audit(
        session,
        action="LOGIN_SUCCESS",
        actor=user.email,
        entity_type="user",
        entity_id=user.id,
        details={"role": user.role, "new_device": is_new_device},
    )
    if is_new_device:
        notifications.notify(
            session,
            user_id=user.id,
            kind="SECURITY_NEW_DEVICE",
            severity="MEDIUM",
            title="New device signed in to your account",
            body=f"A sign-in from device {device} was not seen on your accounts before.",
            entity_type="user",
            entity_id=user.id,
            dedupe_key=f"new-device:{user.id}:{device}",
        )
        session.commit()

    return user, shape_session(session, user, record, token)


def issue_session(
    session: Session, user: User, *, client_host: str = "", user_agent: str = ""
):
    """Create a server-side session and return ``(row, plaintext_token)``."""
    now = utcnow()
    raw_token = new_token()
    record = repo.users.create_session(
        session,
        {
            "id": new_id("SES", 16),
            "user_id": user.id,
            "token_hash": token_digest(raw_token),
            "issued_at": now,
            "expires_at": add_minutes(now, settings.session_ttl_minutes),
            "client_host": (client_host or "")[:60],
            "user_agent": (user_agent or "")[:160],
        },
    )
    return record, raw_token


def resolve_session(session: Session, token: str | None) -> tuple[User, object]:
    """Return ``(user, session_row)`` for a valid token, else raise 401.

    Every request goes through here, which is what makes expiry and revocation
    authoritative rather than advisory.
    """
    if not token:
        raise AuthenticationRequired("Sign in to continue.")
    record = repo.users.get_session_by_token_hash(session, token_digest(token.strip()))
    if record is None:
        raise AuthenticationRequired("Your session is no longer valid. Sign in again.")
    now = utcnow()
    if record.revoked_at is not None:
        raise AuthenticationRequired("You have signed out of this session.")
    if record.expires_at <= now:
        raise AuthenticationRequired("Your session has expired. Sign in again.")
    user = repo.users.get_user(session, record.user_id)
    if user is None or not user.is_active:
        raise AuthenticationRequired("Your session is no longer valid. Sign in again.")
    return user, record


def logout(session: Session, token: str | None, *, user: User | None = None) -> bool:
    """Revoke the presented session. Idempotent: signing out twice is not an error."""
    if not token:
        return False
    record = repo.users.get_session_by_token_hash(session, token_digest(token.strip()))
    if record is None:
        return False
    repo.users.revoke_session(session, record, utcnow())
    session.commit()
    actor = user.email if user is not None else record.user_id
    record_audit(
        session,
        action="LOGOUT",
        actor=actor,
        entity_type="session",
        entity_id=record.id,
    )
    if user is not None:
        record_login_event(
            session,
            user,
            event_type=events.SESSION_REVOKED,
            severity="INFO",
            device_id=DEFAULT_DEVICE,
            country=None,
            details={"reason": "user_signed_out"},
        )
        session.commit()
    return True


def logout_everywhere(session: Session, user: User) -> int:
    revoked = repo.users.revoke_user_sessions(session, user.id, utcnow())
    session.commit()
    record_audit(
        session,
        action="LOGOUT_ALL_SESSIONS",
        actor=user.email,
        entity_type="user",
        entity_id=user.id,
        details={"revoked": revoked},
    )
    return revoked


# ---------------------------------------------------------------------------
# Demo accounts
# ---------------------------------------------------------------------------
def demo_credentials() -> list[dict]:
    """The published demo logins. Configuration, not secrets."""
    if not settings.demo_mode_enabled:
        return []
    return [
        {
            "role": ROLE_CUSTOMER,
            "label": "Customer",
            "email": settings.demo_customer_email,
            "password": settings.demo_customer_password,
            "description": (
                "A fictional retail customer. Sees only their own two accounts, can send "
                "simulated transfers, and can review their own security activity."
            ),
        },
        {
            "role": ROLE_ANALYST,
            "label": "Security analyst",
            "email": settings.demo_analyst_email,
            "password": settings.demo_analyst_password,
            "description": (
                "A fictional security analyst. Sees the whole bank: scenarios, monitoring, "
                "incidents, correlation and model evaluation."
            ),
        },
    ]


def ensure_demo_users(session: Session) -> list[User]:
    """Create the two demo users when they are missing. Idempotent.

    Called on startup so a freshly cloned repository has a working login without a
    separate step, and by the seeder so ``python -m app.db.seed`` is repeatable.
    Existing users are never overwritten, so changing a demo password by hand is
    not silently undone.
    """
    if not settings.demo_mode_enabled:
        return []
    created: list[User] = []
    spec = [
        (DEMO_CUSTOMER_ID, settings.demo_customer_email, "Priya Sharma", ROLE_CUSTOMER,
         settings.demo_customer_password, "Bengaluru", "+91 90•• ••4471"),
        (DEMO_ANALYST_ID, settings.demo_analyst_email, "Arun Menon", ROLE_ANALYST,
         settings.demo_analyst_password, "Mumbai", "+91 98•• ••2210"),
    ]
    for user_id, email, full_name, role, password, city, phone in spec:
        if repo.users.get_user_by_email(session, email) is not None:
            continue
        user = repo.users.add_user(
            session,
            {
                "id": user_id,
                "email": email.lower(),
                "full_name": full_name,
                "role": role,
                "password_hash": hash_password(password),
                "is_demo": True,
                "is_active": True,
            },
        )
        repo.users.add_profile(
            session,
            {
                "id": new_id("PRO"),
                "user_id": user.id,
                "phone_masked": phone,
                "city": city,
                "kyc_status": "VERIFIED",
            },
        )
        created.append(user)
    if created:
        session.commit()
        logger.info("Created %d demo user(s).", len(created))
    return created


def create_user(
    session: Session,
    *,
    email: str,
    full_name: str,
    password: str,
    role: str = ROLE_CUSTOMER,
    is_demo: bool = False,
    city: str = "",
    phone_masked: str = "",
) -> User:
    """Create one user with a hashed password. Used by the seeder and tests."""
    cleaned_email = (email or "").strip().lower()
    if "@" not in cleaned_email or len(cleaned_email) > 120:
        raise ValidationError("email must be a valid address of at most 120 characters.")
    if repo.users.get_user_by_email(session, cleaned_email) is not None:
        raise ValidationError(f"An account already exists for {cleaned_email}.")
    if role not in (ROLE_CUSTOMER, ROLE_ANALYST):
        raise ValidationError("role must be CUSTOMER or ANALYST.")
    if len(password or "") < settings.password_min_length:
        raise ValidationError(
            f"password must be at least {settings.password_min_length} characters."
        )
    user = repo.users.add_user(
        session,
        {
            "id": new_id("USR"),
            "email": cleaned_email,
            "full_name": sanitise_text(full_name, max_length=120, field="full_name"),
            "role": role,
            "password_hash": hash_password(password),
            "is_demo": is_demo,
            "is_active": True,
        },
    )
    repo.users.add_profile(
        session,
        {
            "id": new_id("PRO"),
            "user_id": user.id,
            "phone_masked": phone_masked[:24],
            "city": city[:60],
            "kyc_status": "VERIFIED",
        },
    )
    session.commit()
    return user


def owned_accounts(session: Session, user: User) -> list[Account]:
    return repo.accounts.accounts_for_owner(session, user.id)


__all__ = [
    "DEMO_ANALYST_ID",
    "DEMO_CUSTOMER_ID",
    "authenticate",
    "create_user",
    "demo_credentials",
    "ensure_demo_users",
    "issue_session",
    "logout",
    "logout_everywhere",
    "owned_accounts",
    "primary_account_id",
    "record_login_event",
    "resolve_session",
    "shape_user",
]
