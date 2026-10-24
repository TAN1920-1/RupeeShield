"""SQLAlchemy ORM models — the persistence contract for the whole platform.

Conventions
-----------
* **Money is stored as INTEGER minor units (paise)** in columns suffixed
  ``_minor``. See ``app/utils/money.py`` for the reasoning.
* **Timestamps are stored as naive UTC.** ``app/schemas/common.py`` re-attaches
  the UTC offset when serialising to JSON so API clients always see an explicit
  ``Z``.
* Ground-truth labels (``is_fraud``) come from the *synthetic generator only*.
  The public PaySim-style dataset used for ML has no cybersecurity labels, so
  those labels are never mixed into observed-dataset statistics.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.utils.timeutils import utcnow


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# Simulation control
# ---------------------------------------------------------------------------
class SimulationRun(Base):
    """One execution of an attack scenario against the synthetic bank ledger."""

    __tablename__ = "simulation_runs"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    scenario: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    defense_config: Mapped[str] = mapped_column(String(20), nullable=False, default="COMBINED")
    seed: Mapped[int] = mapped_column(Integer, nullable=False)
    requested_events: Mapped[int] = mapped_column(Integer, nullable=False)
    generated_events: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="PENDING", index=True)
    mode: Mapped[str] = mapped_column(String(10), default="batch")
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    transactions: Mapped[list["Transaction"]] = relationship(back_populates="run")
    events: Mapped[list["SecurityEvent"]] = relationship(back_populates="run")
    detections: Mapped[list["DetectionResult"]] = relationship(back_populates="run")
    incidents: Mapped[list["Incident"]] = relationship(back_populates="run")


class ReplayRun(Base):
    """A read-only replay of a recorded incident (never mutates the original)."""

    __tablename__ = "replay_runs"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    incident_id: Mapped[str] = mapped_column(ForeignKey("incidents.id"), nullable=False, index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    summary: Mapped[dict] = mapped_column(JSON, default=dict)


# ---------------------------------------------------------------------------
# Synthetic bank ledger
# ---------------------------------------------------------------------------
class Account(Base):
    """A synthetic account inside the simulated bank. Never a real customer."""

    __tablename__ = "accounts"

    id: Mapped[str] = mapped_column(String(24), primary_key=True)
    label: Mapped[str] = mapped_column(String(60), nullable=False)
    home_country: Mapped[str] = mapped_column(String(4), nullable=False)
    preferred_channel: Mapped[str] = mapped_column(String(24), nullable=False)
    avg_txn_amount_minor: Mapped[int] = mapped_column(Integer, nullable=False, default=5000)
    account_age_days: Mapped[int] = mapped_column(Integer, nullable=False, default=365)
    opening_balance_minor: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    balance_minor: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Deliberately cautious wording: an account is never labelled as criminal.
    risk_status: Mapped[str] = mapped_column(String(40), default="NORMAL", index=True)
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    transactions: Mapped[list["Transaction"]] = relationship(
        back_populates="account", foreign_keys="Transaction.account_id"
    )


class Transaction(Base):
    """A simulated ledger entry. Amounts are exact integers of paise."""

    __tablename__ = "transactions"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    run_id: Mapped[Optional[str]] = mapped_column(ForeignKey("simulation_runs.id"), nullable=True, index=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), nullable=False, index=True)
    counterparty_id: Mapped[Optional[str]] = mapped_column(ForeignKey("accounts.id"), nullable=True, index=True)
    counterparty_label: Mapped[Optional[str]] = mapped_column(String(60), nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    amount_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="INR", nullable=False)
    channel: Mapped[str] = mapped_column(String(24), nullable=False)
    country: Mapped[str] = mapped_column(String(4), nullable=False)
    merchant_id: Mapped[str] = mapped_column(String(40), nullable=False)
    device_id: Mapped[str] = mapped_column(String(40), nullable=False)
    direction: Mapped[str] = mapped_column(String(8), default="DEBIT", nullable=False)
    # SETTLED | BLOCKED | PENDING_REVIEW | REVERSED  (only SETTLED counts as realized loss)
    status: Mapped[str] = mapped_column(String(20), default="SETTLED", nullable=False, index=True)
    # Synthetic ground truth. NULL means "not labelled" -- never treated as legit.
    is_fraud: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    fraud_type: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    scenario: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    run: Mapped[Optional[SimulationRun]] = relationship(back_populates="transactions")
    account: Mapped[Account] = relationship(back_populates="transactions", foreign_keys=[account_id])

    __table_args__ = (
        Index("ix_transactions_run_ts", "run_id", "timestamp"),
        Index("ix_transactions_account_ts", "account_id", "timestamp"),
    )


class SecurityEvent(Base):
    """Simulated authentication / device / payment-integrity telemetry."""

    __tablename__ = "security_events"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    run_id: Mapped[Optional[str]] = mapped_column(ForeignKey("simulation_runs.id"), nullable=True, index=True)
    incident_id: Mapped[Optional[str]] = mapped_column(ForeignKey("incidents.id"), nullable=True, index=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), nullable=False, index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    severity: Mapped[str] = mapped_column(String(12), default="INFO", nullable=False)
    country: Mapped[Optional[str]] = mapped_column(String(4), nullable=True)
    device_id: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    # Synthetic, non-reversible pseudonym. No real IPs are ever stored.
    source_ip_hash: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    related_transaction_id: Mapped[Optional[str]] = mapped_column(String(40), nullable=True, index=True)
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    run: Mapped[Optional[SimulationRun]] = relationship(back_populates="events")

    __table_args__ = (Index("ix_security_events_account_ts", "account_id", "timestamp"),)


# ---------------------------------------------------------------------------
# Detection / correlation output
# ---------------------------------------------------------------------------
class DetectionResult(Base):
    """One scored decision (transaction-only, rule-only, or combined)."""

    __tablename__ = "detection_results"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    run_id: Mapped[Optional[str]] = mapped_column(ForeignKey("simulation_runs.id"), nullable=True, index=True)
    incident_id: Mapped[Optional[str]] = mapped_column(ForeignKey("incidents.id"), nullable=True, index=True)
    account_id: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    transaction_id: Mapped[Optional[str]] = mapped_column(String(40), nullable=True, index=True)
    security_event_id: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    # Defense configuration that produced this row: NONE | RULE_BASED | COMBINED.
    # NONE still records a row (decision ALLOW, score 0) so monitoring and the
    # defense comparison can see the transactions that went undefended.
    detection_source: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    fraud_risk_score: Mapped[float] = mapped_column(Float, nullable=False)
    decision: Mapped[str] = mapped_column(String(12), nullable=False, index=True)
    ml_supervised_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    ml_anomaly_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    rule_heuristic_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    correlation_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    reasons: Mapped[list] = mapped_column(JSON, default=list)
    rules_triggered: Mapped[list] = mapped_column(JSON, default=list)
    correlated_event_ids: Mapped[list] = mapped_column(JSON, default=list)
    top_features: Mapped[list] = mapped_column(JSON, default=list)
    latency_ms: Mapped[float] = mapped_column(Float, default=0.0)
    # Synthetic ground truth copied from the ledger row, for evaluation only.
    ground_truth_fraud: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    run: Mapped[Optional[SimulationRun]] = relationship(back_populates="detections")

    __table_args__ = (
        Index("ix_detection_source_decision", "detection_source", "decision"),
        Index("ix_detection_run_ts", "run_id", "timestamp"),
    )


# ---------------------------------------------------------------------------
# Incidents
# ---------------------------------------------------------------------------
class Incident(Base):
    __tablename__ = "incidents"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    run_id: Mapped[Optional[str]] = mapped_column(ForeignKey("simulation_runs.id"), nullable=True, index=True)
    scenario: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    primary_account_id: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    summary: Mapped[str] = mapped_column(Text, default="")
    severity: Mapped[str] = mapped_column(String(12), default="MEDIUM", index=True)
    risk_level: Mapped[str] = mapped_column(String(12), default="MEDIUM")
    status: Mapped[str] = mapped_column(String(20), default="OPEN", index=True)
    opened_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    run: Mapped[Optional[SimulationRun]] = relationship(back_populates="incidents")
    timeline: Mapped[list["IncidentEvent"]] = relationship(
        back_populates="incident", order_by="IncidentEvent.seq", cascade="all, delete-orphan"
    )


class IncidentEvent(Base):
    """Ordered timeline entry. Replay reads these in ``seq`` order."""

    __tablename__ = "incident_events"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    incident_id: Mapped[str] = mapped_column(ForeignKey("incidents.id"), nullable=False, index=True)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)  # SECURITY_EVENT|TRANSACTION|DETECTION|DEFENSE
    ref_id: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    description: Mapped[str] = mapped_column(String(240), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)

    incident: Mapped[Incident] = relationship(back_populates="timeline")

    __table_args__ = (Index("ix_incident_events_incident_seq", "incident_id", "seq"),)


class DefenseAction(Base):
    """A defensive response the simulated bank took (or explicitly did not take)."""

    __tablename__ = "defense_actions"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    run_id: Mapped[Optional[str]] = mapped_column(ForeignKey("simulation_runs.id"), nullable=True, index=True)
    incident_id: Mapped[Optional[str]] = mapped_column(ForeignKey("incidents.id"), nullable=True, index=True)
    detection_result_id: Mapped[Optional[str]] = mapped_column(String(40), nullable=True, index=True)
    account_id: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    transaction_id: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    action_type: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    reason: Mapped[str] = mapped_column(String(240), default="")
    amount_minor: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class FinancialImpact(Base):
    """Result of the financial impact engine for one run/incident (all paise)."""

    __tablename__ = "financial_impacts"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    run_id: Mapped[Optional[str]] = mapped_column(ForeignKey("simulation_runs.id"), nullable=True, index=True)
    incident_id: Mapped[Optional[str]] = mapped_column(ForeignKey("incidents.id"), nullable=True, index=True)
    exposure_minor: Mapped[int] = mapped_column(Integer, default=0)
    unauthorized_minor: Mapped[int] = mapped_column(Integer, default=0)
    prevented_minor: Mapped[int] = mapped_column(Integer, default=0)
    # Value of *legitimate* transactions the defense blocked or held: the false-positive
    # cost, expressed in rupees instead of in counts. Never a realized loss.
    interrupted_minor: Mapped[int] = mapped_column(Integer, default=0)
    disruption_cost_minor: Mapped[int] = mapped_column(Integer, default=0)
    recovery_cost_minor: Mapped[int] = mapped_column(Integer, default=0)
    net_impact_minor: Mapped[int] = mapped_column(Integer, default=0)
    assumptions: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class DefenseComparisonRecord(Base):
    """A saved Defense Comparison Lab result (identical inputs, three configurations).

    Stored so the lab's history is inspectable after a page reload instead of
    living only in browser memory.
    """

    __tablename__ = "defense_comparisons"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    scenario: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    seed: Mapped[int] = mapped_column(Integer, nullable=False)
    n_events: Mapped[int] = mapped_column(Integer, nullable=False)
    label_source: Mapped[str] = mapped_column(String(160), nullable=False)
    same_inputs: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    results: Mapped[list] = mapped_column(JSON, default=list)
    conclusion: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


# ---------------------------------------------------------------------------
# Governance
# ---------------------------------------------------------------------------
class ModelVersion(Base):
    """Registered model artefact + the metrics measured when it was trained."""

    __tablename__ = "model_versions"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    version: Mapped[str] = mapped_column(String(20), nullable=False)
    artifact_path: Mapped[str] = mapped_column(String(240), nullable=False)
    trained_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    training_rows: Mapped[int] = mapped_column(Integer, default=0)
    feature_order: Mapped[list] = mapped_column(JSON, default=list)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class AuditLog(Base):
    """Append-only record of every state-changing action and defensive decision."""

    __tablename__ = "audit_logs"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False, index=True)
    actor: Mapped[str] = mapped_column(String(60), default="system", nullable=False)
    action: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(40), default="")
    entity_id: Mapped[Optional[str]] = mapped_column(String(40), nullable=True, index=True)
    outcome: Mapped[str] = mapped_column(String(20), default="SUCCESS")
    details: Mapped[dict] = mapped_column(JSON, default=dict)
