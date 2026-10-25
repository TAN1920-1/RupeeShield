"""Customer banking schemas.

These are the shapes the *customer* sees. They are deliberately narrower than the
analyst schemas in :mod:`app.schemas.transaction`: an account number is masked, the
incident link carries a summary rather than the investigation payload, and the
internal synthetic label ``is_fraud`` is passed through only as a training-data
annotation, never as a claim.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from pydantic import Field

from app.schemas.common import ApiModel, OptionalUtcDateTime, UtcDateTime
from app.schemas.transaction import ShapFeature


class BalanceSummaryOut(ApiModel):
    account_count: int
    currency: str = "INR"
    total_balance_minor: int = Field(description="Sum of the customer's account balances, in paise.")
    total_balance: float
    held_minor: int = Field(
        default=0, description="Outgoing value awaiting a decision; not yet deducted."
    )
    held: float = 0.0
    available_minor: int
    available: float


class AccountCardOut(ApiModel):
    id: str
    label: str
    masked_number: str = Field(description="Masked account number. The full number is never generated.")
    account_type: str
    currency: str = "INR"
    status: str = Field(description="ACTIVE | UNDER_REVIEW")
    status_label: str
    balance_minor: int
    balance: float
    opening_balance_minor: int
    home_country: str
    preferred_channel: str
    account_age_days: int
    risk_status: str
    transaction_count: int = 0
    suspicious_count: int = 0
    pending_count: int = 0
    last_activity_at: OptionalUtcDateTime = None
    is_synthetic: bool = True
    is_primary: bool = False


class AccountListOut(ApiModel):
    items: list[AccountCardOut] = Field(default_factory=list)
    summary: BalanceSummaryOut
    total: int
    limit: int
    offset: int
    returned: int


class CustomerTransactionOut(ApiModel):
    id: str
    timestamp: UtcDateTime
    description: str = Field(description="Customer-facing memo; composed from real row fields when absent.")
    direction: str = Field(description="DEBIT (outgoing) | CREDIT (incoming).")
    sender: str | None = None
    recipient: str | None = None
    counterparty_id: str | None = None
    counterparty_label: str | None = None
    account_id: str
    account_label: str | None = None
    account_masked_number: str = ""
    amount_minor: int
    amount: float
    signed_amount_minor: int
    signed_amount: float
    currency: str = "INR"
    channel: str
    status: str
    status_label: str
    decision: str | None = None
    risk_score: float | None = None
    risk_level: str | None = None
    suspicious: bool = False
    detection_id: str | None = None
    incident_id: str | None = None
    is_fraud: int | None = Field(
        default=None, description="Synthetic generator label, used for evaluation only."
    )


class CustomerIncidentOut(ApiModel):
    """The customer-safe view of an incident on one of their accounts."""

    id: str
    title: str
    status: str
    severity: str
    risk_level: str
    scenario: str | None = None
    scenario_name: str = ""
    primary_account_id: str
    primary_account_label: str | None = None
    opened_at: UtcDateTime
    closed_at: OptionalUtcDateTime = None
    event_count: int = 0
    transaction_count: int = 0
    suspected_loss_minor: int = 0
    suspected_loss: float = 0.0


class CustomerTransactionDetailOut(CustomerTransactionOut):
    detection: dict[str, Any] | None = None
    explanation: list[ShapFeature] = Field(default_factory=list)
    security_events: list[dict[str, Any]] = Field(default_factory=list)
    incident: CustomerIncidentOut | None = None
    related: list[dict[str, Any]] = Field(default_factory=list)


class TransferRequest(ApiModel):
    source_account_id: str = Field(..., min_length=1, max_length=64)
    recipient_account_id: str = Field(..., min_length=1, max_length=64)
    amount: Decimal = Field(
        ...,
        gt=0,
        max_digits=14,
        decimal_places=2,
        description="Major units (INR) with at most 2 decimal places. Converted to paise exactly.",
    )
    description: str = Field(default="", max_length=140)
    idempotency_key: str | None = Field(
        default=None,
        max_length=80,
        description="Client-generated key. Replaying it returns the original transfer instead of moving money twice.",
    )


class TransferLegOut(ApiModel):
    account_id: str
    label: str
    direction: str
    amount_minor: int
    amount: float
    balance_minor: int
    balance: float
    masked_number: str


class TransferResultOut(ApiModel):
    transfer_id: str
    status: str
    status_label: str
    decision: str
    risk_score: float
    reasons: list[str] = Field(default_factory=list)
    rules_triggered: list[str] = Field(default_factory=list)
    message: str
    duplicate: bool = False
    settled: bool = False
    amount_minor: int
    amount: float
    currency: str = "INR"
    description: str = ""
    created_at: UtcDateTime
    source: TransferLegOut
    recipient: TransferLegOut
    transactions: list[CustomerTransactionOut] = Field(default_factory=list)
    notification_id: str | None = None


class SecurityFactorOut(ApiModel):
    key: str
    label: str
    value: int
    tone: str = Field(description="GOOD | ATTENTION | DANGER — never colour alone.")
    detail: str


class SecurityStatusOut(ApiModel):
    level: str = Field(description="GOOD | ATTENTION | ELEVATED")
    label: str
    summary: str


class SignInEventOut(ApiModel):
    id: str
    timestamp: UtcDateTime
    event_type: str
    description: str
    severity: str
    device_id: str | None = None
    country: str | None = None
    source_ip_hash: str | None = None


class DeviceOut(ApiModel):
    device_id: str
    country: str | None = None
    last_seen: OptionalUtcDateTime = None
    recognised: bool = True
    detail: str = ""


class SecurityCentreOut(ApiModel):
    status: SecurityStatusOut
    factors: list[SecurityFactorOut] = Field(default_factory=list)
    recent_sign_ins: list[SignInEventOut] = Field(default_factory=list)
    failed_sign_ins: list[SignInEventOut] = Field(default_factory=list)
    devices: list[DeviceOut] = Field(default_factory=list)
    suspicious_transactions: list[CustomerTransactionOut] = Field(default_factory=list)
    open_incidents: list[CustomerIncidentOut] = Field(default_factory=list)
    security_events: list[SignInEventOut] = Field(default_factory=list)
    generated_at: UtcDateTime


class PayeeOut(ApiModel):
    id: str
    label: str
    masked_number: str
    account_type: str
    currency: str = "INR"


class SimulatedLoginOut(ApiModel):
    message: str
    event: SignInEventOut
    device_id: str
    country: str
    notification_id: str | None = None


__all__ = [
    "AccountCardOut",
    "AccountListOut",
    "BalanceSummaryOut",
    "CustomerIncidentOut",
    "CustomerTransactionDetailOut",
    "CustomerTransactionOut",
    "DeviceOut",
    "PayeeOut",
    "SecurityCentreOut",
    "SecurityFactorOut",
    "SecurityStatusOut",
    "SignInEventOut",
    "SimulatedLoginOut",
    "TransferLegOut",
    "TransferRequest",
    "TransferResultOut",
]
