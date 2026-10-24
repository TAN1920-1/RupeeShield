"""Detection service: turns one transaction's features into a recorded decision.

This is the only place that knows how a defense configuration maps onto scoring, so
the live scorer, the simulation engine and the Defense Comparison Lab all produce
identical numbers for identical input.

Configurations
--------------
``NONE``
    No automated defense. Nothing is scored and every transaction settles. Rows are
    still written (score 0, decision ALLOW) so the lab can count what was missed.
``RULE_BASED``
    Heuristic rules only. No ML, no security-event correlation.
``COMBINED``
    Rules + supervised ML + anomaly detection + security-event correlation.

Correlation only ever *raises* risk: ``final = transaction_risk + 0.5 x correlation``
clamped to 1.0. Keeping it additive (rather than a weighted average) makes the
contribution easy to explain in a viva: "the transaction scored 0.52; the correlated
login burst added 0.225, which pushed it over the 0.80 block threshold."
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

from app.core.logging import get_logger
from app.ml.model_store import ModelArtifactError, models_available
from app.ml.predict import get_predictor
from app.ml.risk_engine import decide
from app.services.correlation import CorrelationResult

logger = get_logger(__name__)

DEFENSE_CONFIGS = ["NONE", "RULE_BASED", "COMBINED"]

#: How strongly a firing correlation rule is allowed to move the risk score.
CORRELATION_BOOST = 0.5

#: Decision -> ledger/response status.
RESPONSE_BY_DECISION = {
    "BLOCK": "BLOCKED",
    "FLAG": "PENDING_REVIEW",
    "ALLOW": "SETTLED",
}

NO_DEFENSE_REASON = "No automated defense configured: the transaction settled without being scored."


class DetectionUnavailable(RuntimeError):
    """Raised when a configuration needs the ML models and they are not on disk."""


@dataclass
class DecisionRecord:
    """Everything one scored transaction produced."""

    detection_source: str
    transaction_risk_score: float
    fraud_risk_score: float
    decision: str
    response: str
    ml_supervised_score: float | None = None
    ml_anomaly_score: float | None = None
    rule_heuristic_score: float | None = None
    correlation_score: float | None = None
    reasons: list[str] = field(default_factory=list)
    rules_triggered: list[str] = field(default_factory=list)
    correlated_event_ids: list[str] = field(default_factory=list)
    top_features: list[dict] = field(default_factory=list)
    latency_ms: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


def models_ready() -> bool:
    return models_available()


def evaluate_transaction(
    features: dict,
    *,
    defense_config: str = "COMBINED",
    correlation: CorrelationResult | None = None,
    explain: bool = False,
) -> DecisionRecord:
    """Score one transaction under a defense configuration.

    Raises :class:`DetectionUnavailable` when ``COMBINED`` is requested but the
    trained artefacts are missing. Callers must decide explicitly what to do
    (the simulation engine downgrades to rule-only and says so in the run summary;
    the API returns a 409 with setup instructions). Fabricating a score is never an
    option.
    """
    config = (defense_config or "COMBINED").upper()
    if config not in DEFENSE_CONFIGS:
        raise ValueError(f"Unknown defense configuration '{defense_config}'.")

    if config == "NONE":
        return DecisionRecord(
            detection_source="NONE",
            transaction_risk_score=0.0,
            fraud_risk_score=0.0,
            decision="ALLOW",
            response="SETTLED",
            reasons=[NO_DEFENSE_REASON],
        )

    if config == "COMBINED" and not models_ready():
        raise DetectionUnavailable(
            "The ML artefacts are missing, so the COMBINED configuration cannot run. "
            "Train them first:\n"
            "    python scripts/generate_dataset.py\n"
            "    python -m app.ml.train"
        )

    try:
        predictor = get_predictor()
    except ModelArtifactError as exc:  # pragma: no cover - guarded by models_ready()
        raise DetectionUnavailable(str(exc)) from exc

    use_ml = config == "COMBINED"
    scored = predictor.score(features, use_ml=use_ml, explain=explain)
    risk = scored.result

    reasons = list(risk.reasons)
    rules = list(risk.rules_triggered)
    correlation_score: float | None = None
    evidence: list[str] = []
    final_score = risk.fraud_risk_score

    if config == "COMBINED" and correlation is not None and correlation.triggered:
        correlation_score = correlation.score
        rules = sorted(set(rules + correlation.rules))
        reasons = reasons + correlation.reasons
        evidence = list(correlation.evidence_event_ids)
        boosted = min(risk.fraud_risk_score + CORRELATION_BOOST * correlation.score, 1.0)
        boosted = round(boosted, 4)
        if boosted > risk.fraud_risk_score:
            reasons.append(
                f"Security-event correlation added {CORRELATION_BOOST * correlation.score:.2f} "
                f"to the transaction risk score ({risk.fraud_risk_score:.2f} -> {boosted:.2f})"
            )
        final_score = boosted

    decision = decide(final_score)

    return DecisionRecord(
        detection_source=config,
        transaction_risk_score=risk.fraud_risk_score,
        fraud_risk_score=final_score,
        decision=decision,
        response=RESPONSE_BY_DECISION[decision],
        ml_supervised_score=risk.ml_supervised_score,
        ml_anomaly_score=risk.ml_anomaly_score,
        rule_heuristic_score=risk.rule_heuristic_score,
        correlation_score=correlation_score,
        reasons=reasons,
        rules_triggered=rules,
        correlated_event_ids=evidence,
        top_features=scored.top_features,
        latency_ms=scored.latency_ms,
    )


def config_catalog() -> list[dict]:
    """The three configurations the Defense Comparison Lab runs, with their meaning."""
    return [
        {
            "key": "NONE",
            "name": "No automated defense",
            "description": "Transactions settle unexamined. This is the baseline every other "
                           "configuration is measured against.",
            "uses_ml": False,
            "uses_correlation": False,
        },
        {
            "key": "RULE_BASED",
            "name": "Rule-based defense",
            "description": "Auditable heuristics only (velocity, amount outliers, new device or "
                           "country). Fast and explainable, but blind to anything a rule does not name.",
            "uses_ml": False,
            "uses_correlation": False,
        },
        {
            "key": "COMBINED",
            "name": "Rule-based + ML + security-event correlation",
            "description": "Blends supervised ML (known typologies), unsupervised anomaly detection "
                           "(novel patterns) and correlated authentication/payment telemetry.",
            "uses_ml": True,
            "uses_correlation": True,
        },
    ]
