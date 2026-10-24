"""Canonical security-event vocabulary.

Both the simulation engine (which *emits* these events) and the correlation engine
(which *interprets* them) import this module, so an event can never be written with
a name the correlation rules do not know about.

These are simulated telemetry events. They describe what the synthetic bank's
systems observed, not what a real person did.
"""
from __future__ import annotations

# -- authentication ---------------------------------------------------------
FAILED_LOGIN = "FAILED_LOGIN"
LOGIN_SUCCESS = "LOGIN_SUCCESS"
LOGIN_NEW_DEVICE = "LOGIN_NEW_DEVICE"
LOGIN_FOREIGN_IP = "LOGIN_FOREIGN_IP"
IMPOSSIBLE_TRAVEL = "IMPOSSIBLE_TRAVEL"
PASSWORD_RESET = "PASSWORD_RESET"

# -- device / session -------------------------------------------------------
NEW_DEVICE = "NEW_DEVICE"
DEVICE_REPUTATION_LOW = "DEVICE_REPUTATION_LOW"

# -- payment integrity ------------------------------------------------------
PAYMENT_DETAILS_MODIFIED = "PAYMENT_DETAILS_MODIFIED"
BENEFICIARY_ADDED = "BENEFICIARY_ADDED"
BENEFICIARY_CHANGED = "BENEFICIARY_CHANGED"
PAYMENT_AMOUNT_TAMPERED = "PAYMENT_AMOUNT_TAMPERED"

# -- transfer behaviour -----------------------------------------------------
NEW_BENEFICIARY = "NEW_BENEFICIARY"
MULE_FUNDING_RECEIVED = "MULE_FUNDING_RECEIVED"
RAPID_FANOUT = "RAPID_FANOUT"
CARD_TESTING_PROBE = "CARD_TESTING_PROBE"

# -- response ---------------------------------------------------------------
ACCOUNT_SUSPENDED = "ACCOUNT_SUSPENDED"
SESSION_REVOKED = "SESSION_REVOKED"

ALL_EVENT_TYPES = [
    FAILED_LOGIN,
    LOGIN_SUCCESS,
    LOGIN_NEW_DEVICE,
    LOGIN_FOREIGN_IP,
    IMPOSSIBLE_TRAVEL,
    PASSWORD_RESET,
    NEW_DEVICE,
    DEVICE_REPUTATION_LOW,
    PAYMENT_DETAILS_MODIFIED,
    BENEFICIARY_ADDED,
    BENEFICIARY_CHANGED,
    PAYMENT_AMOUNT_TAMPERED,
    NEW_BENEFICIARY,
    MULE_FUNDING_RECEIVED,
    RAPID_FANOUT,
    CARD_TESTING_PROBE,
    ACCOUNT_SUSPENDED,
    SESSION_REVOKED,
]

SEVERITIES = ["INFO", "LOW", "MEDIUM", "HIGH", "CRITICAL"]

#: Event types that indicate a compromised credential or session.
AUTH_COMPROMISE_EVENTS = frozenset(
    {FAILED_LOGIN, LOGIN_NEW_DEVICE, LOGIN_FOREIGN_IP, IMPOSSIBLE_TRAVEL, PASSWORD_RESET}
)

#: Event types that indicate an attempt to redirect money while it moves.
PAYMENT_INTEGRITY_EVENTS = frozenset(
    {PAYMENT_DETAILS_MODIFIED, BENEFICIARY_ADDED, BENEFICIARY_CHANGED, PAYMENT_AMOUNT_TAMPERED}
)
