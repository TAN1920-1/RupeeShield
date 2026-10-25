"""Read/write helpers for users, customer profiles and sessions."""
from __future__ import annotations

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.db.models import AuthSession, CustomerProfile, User


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------
def add_user(session: Session, row: dict) -> User:
    user = User(**row)
    session.add(user)
    session.flush()
    return user


def add_profile(session: Session, row: dict) -> CustomerProfile:
    profile = CustomerProfile(**row)
    session.add(profile)
    session.flush()
    return profile


def get_user(session: Session, user_id: str) -> User | None:
    return session.get(User, user_id)


def get_user_by_email(session: Session, email: str) -> User | None:
    """Case-insensitive lookup so ``Priya@…`` and ``priya@…`` are the same account."""
    candidate = (email or "").strip().lower()
    if not candidate:
        return None
    return session.execute(
        select(User).where(func.lower(User.email) == candidate)
    ).scalars().first()


def list_users(session: Session, *, role: str | None = None) -> list[User]:
    statement = select(User).order_by(User.role, User.email)
    if role:
        statement = statement.where(User.role == role)
    return list(session.execute(statement).scalars())


def count_users(session: Session) -> int:
    return int(session.execute(select(func.count()).select_from(User)).scalar() or 0)


def profile_for(session: Session, user_id: str) -> CustomerProfile | None:
    return session.execute(
        select(CustomerProfile).where(CustomerProfile.user_id == user_id)
    ).scalars().first()


def register_login_success(session: Session, user: User, when) -> None:
    user.last_login_at = when
    user.failed_login_count = 0
    session.add(user)


def register_login_failure(session: Session, user: User) -> int:
    """Count one consecutive failure and return the running total."""
    user.failed_login_count = int(user.failed_login_count or 0) + 1
    session.add(user)
    session.flush()
    return int(user.failed_login_count)


def clear_login_failures(session: Session, user: User) -> None:
    if user.failed_login_count:
        user.failed_login_count = 0
        session.add(user)


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------
def create_session(session: Session, row: dict) -> AuthSession:
    record = AuthSession(**row)
    session.add(record)
    session.flush()
    return record


def get_session_by_token_hash(session: Session, token_hash: str) -> AuthSession | None:
    return session.execute(
        select(AuthSession).where(AuthSession.token_hash == token_hash)
    ).scalars().first()


def revoke_session(session: Session, record: AuthSession, when) -> None:
    if record.revoked_at is None:
        record.revoked_at = when
        session.add(record)


def revoke_user_sessions(session: Session, user_id: str, when) -> int:
    """Revoke every live session for one user (used by logout-everywhere)."""
    records = session.execute(
        select(AuthSession).where(
            AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None)
        )
    ).scalars().all()
    for record in records:
        record.revoked_at = when
        session.add(record)
    return len(records)


def active_sessions(session: Session, user_id: str, when) -> list[AuthSession]:
    return list(
        session.execute(
            select(AuthSession)
            .where(
                AuthSession.user_id == user_id,
                AuthSession.revoked_at.is_(None),
                AuthSession.expires_at > when,
            )
            .order_by(AuthSession.issued_at.desc())
        ).scalars()
    )


def purge_expired_sessions(session: Session, before) -> int:
    """Housekeeping: drop rows that can no longer authenticate anything."""
    result = session.execute(
        delete(AuthSession).where(AuthSession.expires_at <= before)
    )
    return int(result.rowcount or 0)
