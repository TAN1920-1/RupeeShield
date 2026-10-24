"""Security-event correlation rules.

Correlation is the component the project's research question hinges on, so each
rule is tested in isolation with a crafted event list and a pinned expected score:
the rule must fire exactly when its documented condition holds and contribute
exactly its weight from ``RULE_WEIGHTS``. The tests also pin the two properties the
design leans on — correlation can only *raise* risk, and a burst of identical
alerts collapses to one.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.core.config import ThresholdSettings
from app.services import event_types as events
from app.services.correlation import (
    RULE_WEIGHTS,
    SecurityEventView,
    TransactionView,
    correlate,
    dedupe_alerts,
)

#: A fixed reference instant. Everything is relative to it so no wall clock leaks in.
REF = datetime(2025, 3, 1, 12, 0, 0)
ACCOUNT = "ACC-0001"


def _event(
    event_id: str,
    event_type: str,
    *,
    minutes_before: float = 1.0,
    account_id: str = ACCOUNT,
    device_id: str | None = None,
    country: str | None = None,
    severity: str = "INFO",
) -> SecurityEventView:
    return SecurityEventView(
        id=event_id,
        account_id=account_id,
        timestamp=REF - timedelta(minutes=minutes_before),
        event_type=event_type,
        severity=severity,
        country=country,
        device_id=device_id,
    )


def _txn(amount_minor: int = 100_000, *, account_id: str = ACCOUNT) -> TransactionView:
    return TransactionView(account_id=account_id, timestamp=REF, amount_minor=amount_minor)


def _correlate(events, *, txn=None, **kwargs):
    return correlate(txn or _txn(), events, thresholds=ThresholdSettings(), **kwargs)


# ---------------------------------------------------------------------------
# One test per documented rule: fires exactly when the condition holds.
# ---------------------------------------------------------------------------
def test_failed_login_burst_rule_fires_at_threshold_and_not_below():
    burst = [_event(f"S{i}", events.FAILED_LOGIN, minutes_before=i) for i in range(1, 6)]
    result = _correlate(burst)
    assert "C-FAILED-LOGIN-BURST" in result.rules
    assert result.score == RULE_WEIGHTS["C-FAILED-LOGIN-BURST"]

    below = burst[:-1]
    below_result = _correlate(below)
    assert "C-FAILED-LOGIN-BURST" not in below_result.rules
    assert below_result.score == 0.0


def test_credential_compromise_rule_fires_on_unusual_login_only():
    for event_type in (events.LOGIN_NEW_DEVICE, events.LOGIN_FOREIGN_IP, events.IMPOSSIBLE_TRAVEL):
        result = _correlate([_event("S1", event_type)])
        assert "C-CREDENTIAL-COMPROMISE" in result.rules, event_type
        assert result.score == RULE_WEIGHTS["C-CREDENTIAL-COMPROMISE"]

    # A plain successful login is not a compromise signal.
    benign = _correlate([_event("S1", events.LOGIN_SUCCESS)])
    assert "C-CREDENTIAL-COMPROMISE" not in benign.rules
    assert benign.score == 0.0


def test_new_device_high_value_rule_needs_both_a_new_device_and_a_large_amount():
    high = _correlate([_event("S1", events.NEW_DEVICE)], txn=_txn(40_000))
    assert "C-NEW-DEVICE-HIGH-VALUE" in high.rules
    assert high.score == RULE_WEIGHTS["C-NEW-DEVICE-HIGH-VALUE"]

    just_below = _correlate([_event("S1", events.NEW_DEVICE)], txn=_txn(39_999))
    assert "C-NEW-DEVICE-HIGH-VALUE" not in just_below.rules

    no_device_event = _correlate([], txn=_txn(500_000))
    assert "C-NEW-DEVICE-HIGH-VALUE" not in no_device_event.rules


def test_payment_tampering_rule_fires_for_every_integrity_event():
    for event_type in events.PAYMENT_INTEGRITY_EVENTS:
        result = _correlate([_event("S1", event_type)])
        assert "C-PAYMENT-TAMPERING" in result.rules, event_type
        assert result.score == RULE_WEIGHTS["C-PAYMENT-TAMPERING"]


def test_coordinated_fanout_rule_fires_on_new_beneficiary_events():
    from_events = _correlate(
        [
            _event("S1", events.NEW_BENEFICIARY),
            _event("S2", events.NEW_BENEFICIARY),
            _event("S3", events.NEW_BENEFICIARY),
        ]
    )
    assert "C-COORDINATED-FANOUT" in from_events.rules
    assert from_events.score == RULE_WEIGHTS["C-COORDINATED-FANOUT"]

    too_few = _correlate([_event("S1", events.NEW_BENEFICIARY), _event("S2", events.NEW_BENEFICIARY)])
    assert "C-COORDINATED-FANOUT" not in too_few.rules


def test_coordinated_fanout_rule_fires_on_distinct_beneficiary_ids_alone():
    # The rule's condition is disjunctive: either the events show the fan-out, or the
    # caller's wider view shows enough distinct new beneficiaries. Both routes must
    # raise the alert — the no-evidence route carries its justification in ``reasons``
    # because the ids it fired on are beneficiaries, not security events.
    from_ids = _correlate([], new_beneficiary_ids={"B1", "B2", "B3"})
    assert "C-COORDINATED-FANOUT" in from_ids.rules
    assert from_ids.score == RULE_WEIGHTS["C-COORDINATED-FANOUT"]
    assert from_ids.evidence_event_ids == []
    assert from_ids.reasons


def test_shared_device_rule_uses_either_the_events_or_the_wider_view():
    with_event = _correlate(
        [_event("S1", events.FAILED_LOGIN, device_id="D-SHARED")],
        shared_device_accounts={ACCOUNT},
    )
    assert "C-SHARED-DEVICE" in with_event.rules
    assert with_event.score == RULE_WEIGHTS["C-SHARED-DEVICE"]
    assert with_event.evidence_event_ids == ["S1"]

    # The account itself is in the shared set even though no *device-carrying* event
    # was supplied: the wider-view signal is enough to raise the rule.
    no_event = _correlate([_event("S1", events.FAILED_LOGIN)], shared_device_accounts={ACCOUNT})
    assert "C-SHARED-DEVICE" in no_event.rules
    assert no_event.evidence_event_ids == []

    not_shared = _correlate([_event("S1", events.FAILED_LOGIN, device_id="D1")])
    assert "C-SHARED-DEVICE" not in not_shared.rules


def test_mule_funding_rule_fires_on_inbound_wire_event():
    result = _correlate([_event("S1", events.MULE_FUNDING_RECEIVED)])
    assert "C-MULE-FUNDING" in result.rules
    assert result.score == RULE_WEIGHTS["C-MULE-FUNDING"]


def test_card_testing_rule_fires_on_probe_events():
    result = _correlate([_event("S1", events.CARD_TESTING_PROBE)])
    assert "C-CARD-TESTING" in result.rules
    assert result.score == RULE_WEIGHTS["C-CARD-TESTING"]


# ---------------------------------------------------------------------------
# Score arithmetic and evidence
# ---------------------------------------------------------------------------
def test_scores_are_the_additive_sum_of_triggered_rule_weights():
    events_in = [
        _event("P1", events.PAYMENT_DETAILS_MODIFIED),
        _event("M1", events.MULE_FUNDING_RECEIVED),
    ]
    result = _correlate(events_in)
    assert set(result.rules) == {"C-PAYMENT-TAMPERING", "C-MULE-FUNDING"}
    expected = RULE_WEIGHTS["C-PAYMENT-TAMPERING"] + RULE_WEIGHTS["C-MULE-FUNDING"]
    assert result.score == round(expected, 4)


def test_additive_score_is_clamped_at_one():
    events_in = [
        _event(f"L{i}", events.FAILED_LOGIN, minutes_before=i) for i in range(1, 6)
    ] + [
        _event("C1", events.LOGIN_NEW_DEVICE),
        _event("D1", events.NEW_DEVICE),
        _event("P1", events.PAYMENT_DETAILS_MODIFIED),
        _event("N1", events.NEW_BENEFICIARY),
        _event("N2", events.NEW_BENEFICIARY),
        _event("N3", events.NEW_BENEFICIARY),
        _event("F1", events.MULE_FUNDING_RECEIVED),
        _event("T1", events.CARD_TESTING_PROBE),
    ]
    result = _correlate(events_in, txn=_txn(500_000))
    raw = sum(RULE_WEIGHTS[code] for code in result.rules)
    assert raw > 1.0
    assert result.score == 1.0


def test_two_strong_rules_outrank_one():
    one = _correlate([_event(f"L{i}", events.FAILED_LOGIN, minutes_before=i) for i in range(1, 6)])
    two = _correlate(
        [
            _event(f"L{i}", events.FAILED_LOGIN, minutes_before=i)
            for i in range(1, 6)
        ]
        + [_event("P1", events.PAYMENT_DETAILS_MODIFIED)]
    )
    assert one.rules == ["C-FAILED-LOGIN-BURST"]
    assert set(two.rules) == {"C-FAILED-LOGIN-BURST", "C-PAYMENT-TAMPERING"}
    assert two.score > one.score


def test_evidence_ids_are_provenance_for_every_triggered_rule():
    events_in = [
        _event("L1", events.FAILED_LOGIN, minutes_before=1),
        _event("L2", events.FAILED_LOGIN, minutes_before=2),
        _event("L3", events.FAILED_LOGIN, minutes_before=3),
        _event("L4", events.FAILED_LOGIN, minutes_before=4),
        _event("L5", events.FAILED_LOGIN, minutes_before=5),
        _event("P1", events.PAYMENT_DETAILS_MODIFIED, minutes_before=2),
    ]
    result = _correlate(events_in)
    assert result.rules
    assert result.evidence_event_ids
    # Unique, sorted, and every id is one of the inputs.
    assert result.evidence_event_ids == sorted(set(result.evidence_event_ids))
    assert set(result.evidence_event_ids).issubset({e.id for e in events_in})
    # The failed-login rule's five events and the tampering event are all cited.
    assert {"L1", "L2", "L3", "L4", "L5", "P1"}.issubset(set(result.evidence_event_ids))


def test_lookback_window_excludes_events_that_are_too_old():
    window = ThresholdSettings().correlation_lookback_minutes
    just_inside = _correlate([_event("S1", events.PAYMENT_DETAILS_MODIFIED, minutes_before=window - 1)])
    assert "C-PAYMENT-TAMPERING" in just_inside.rules

    too_old = _correlate([_event("S1", events.PAYMENT_DETAILS_MODIFIED, minutes_before=window + 1)])
    assert "C-PAYMENT-TAMPERING" not in too_old.rules
    assert too_old.score == 0.0


def test_correlation_only_raises_risk_and_a_clean_transaction_is_not_flagged():
    clean = _correlate([])
    assert clean.rules == []
    assert clean.score == 0.0
    assert clean.triggered is False

    risky = _correlate([_event("P1", events.PAYMENT_DETAILS_MODIFIED)])
    assert risky.triggered is True
    assert risky.score > clean.score


# ---------------------------------------------------------------------------
# Alert de-duplication
# ---------------------------------------------------------------------------
def test_dedupe_alerts_collapses_repeats_and_reports_suppressed_count():
    alerts = [
        {"account_id": "A", "code": "C-X", "timestamp": REF},
        {"account_id": "A", "code": "C-X", "timestamp": REF + timedelta(minutes=5)},
        {"account_id": "A", "code": "C-Y", "timestamp": REF + timedelta(minutes=5)},
        {"account_id": "B", "code": "C-X", "timestamp": REF + timedelta(minutes=5)},
        {"account_id": "A", "code": "C-X", "timestamp": REF + timedelta(minutes=12)},
        {"account_id": "A", "code": "C-X", "timestamp": REF + timedelta(minutes=40)},
    ]
    kept = dedupe_alerts(alerts, window_minutes=15)

    # A/C-X at t0 absorbs the t+5 and t+12 repeats; the t+40 one is a fresh alert.
    assert len(kept) == 4
    suppressed = sum(alert["duplicates"] for alert in kept)
    assert suppressed == 2

    first = next(alert for alert in kept if alert["account_id"] == "A" and alert["code"] == "C-X"
                 and alert["timestamp"] == REF)
    assert first["duplicates"] == 2

    pairs = {(alert["account_id"], alert["code"]) for alert in kept}
    assert pairs == {("A", "C-X"), ("A", "C-Y"), ("B", "C-X")}
