"""Simulation scenarios and the run engine.

Two levels are tested. First, ``build_plan`` must be a *pure, deterministic*
function of ``(scenario, seed, n_events)``: the Defense Comparison Lab only means
anything if the same script is replayed under each defense configuration. Second,
the engine must persist a self-consistent ledger: every transaction has a
detection, incidents belong to the run and start ``OPEN``, blocked transactions are
never counted as realized loss, and every money column is integer paise.
"""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.db import repositories as repo
from app.db.models import DefenseAction, DetectionResult, Transaction
from app.services.simulation import catalog
from app.services.simulation.scenarios import build_plan
from app.services.simulation_runner import create_run

SCENARIO = "ACCOUNT_TAKEOVER"
SEED = 4242
EVENTS = 40

TERMINAL_STATUSES = {"COMPLETED", "STOPPED", "FAILED"}
STOPPED_STATUSES = {"BLOCKED", "PENDING_REVIEW"}


# ---------------------------------------------------------------------------
# build_plan determinism and validation
# ---------------------------------------------------------------------------
def test_build_plan_is_deterministic_for_the_same_inputs():
    first = build_plan(SCENARIO, seed=SEED, n_events=EVENTS)
    second = build_plan(SCENARIO, seed=SEED, n_events=EVENTS)

    assert first.fingerprint == second.fingerprint
    assert len(first.transactions) == len(second.transactions)
    assert len(first.security_events) == len(second.security_events)
    assert len(first.attack_transactions) == len(second.attack_transactions)
    assert len(first.cover_transactions) == len(second.cover_transactions)


def test_build_plan_changes_when_the_seed_changes():
    baseline = build_plan(SCENARIO, seed=SEED, n_events=EVENTS)
    other = build_plan(SCENARIO, seed=SEED + 1, n_events=EVENTS)
    assert baseline.fingerprint != other.fingerprint


def test_build_plan_rejects_an_unknown_scenario():
    with pytest.raises(KeyError) as excinfo:
        build_plan("NOT_A_SCENARIO", seed=SEED, n_events=EVENTS)
    assert "NOT_A_SCENARIO" in str(excinfo.value)


def test_build_plan_rejects_too_few_events():
    with pytest.raises(ValueError):
        build_plan(SCENARIO, seed=SEED, n_events=4)


def test_every_scenario_has_attack_and_cover_traffic_with_labelled_steps():
    for key in catalog.SCENARIO_KEYS:
        plan = build_plan(key, seed=7, n_events=EVENTS)

        assert plan.attack_transactions, key
        assert plan.cover_transactions, key
        # Cover traffic is ordinary activity: it carries no fraud label.
        assert all(txn.is_fraud == 0 for txn in plan.cover_transactions), key
        # Every attack transaction belongs to a named fraud type (the "attack step").
        assert all(txn.fraud_type for txn in plan.attack_transactions), key
        assert all(txn.is_fraud == 1 for txn in plan.attack_transactions), key


# ---------------------------------------------------------------------------
# Engine invariants against a real database session
# ---------------------------------------------------------------------------
def _transactions(session, run_id):
    session.expire_all()
    return list(
        session.execute(
            select(Transaction).where(Transaction.run_id == run_id).order_by(Transaction.id)
        ).scalars()
    )


def _detections(session, run_id):
    session.expire_all()
    return list(
        session.execute(select(DetectionResult).where(DetectionResult.run_id == run_id)).scalars()
    )


def _defense_actions(session, run_id):
    session.expire_all()
    return list(
        session.execute(select(DefenseAction).where(DefenseAction.run_id == run_id)).scalars()
    )


def test_no_defense_configuration_blocks_nothing_and_records_no_defense_action(session):
    run = create_run(
        session, scenario=SCENARIO, n_events=EVENTS, seed=SEED, defense_config="NONE"
    )
    run_id = run["id"]

    transactions = _transactions(session, run_id)
    assert transactions
    assert all(txn.status == "SETTLED" for txn in transactions)
    assert _defense_actions(session, run_id) == []


def test_rule_based_configuration_stops_fraud_and_stopped_value_is_not_a_realized_loss(session):
    run = create_run(
        session, scenario=SCENARIO, n_events=EVENTS, seed=SEED, defense_config="RULE_BASED"
    )
    run_id = run["id"]

    transactions = _transactions(session, run_id)
    assert transactions
    # A rule-only defense holds suspicious transfers for review (PENDING_REVIEW) and,
    # when a rule reaches the block band, blocks outright. Either way, whatever it
    # stops must be excluded from the realized-loss bucket.
    stopped = [txn for txn in transactions if txn.status in STOPPED_STATUSES]
    stopped_fraud = [txn for txn in stopped if txn.is_fraud == 1]
    assert stopped, "the rule-based defense should stop at least one scripted attack transfer"
    assert stopped_fraud, "the stopped transactions should include scripted fraud"

    session.expire_all()
    impact = repo.governance.financial_impact_for_run(session, run_id)
    assert impact is not None

    expected_unauthorized = sum(
        txn.amount_minor for txn in transactions if txn.status == "SETTLED" and txn.is_fraud == 1
    )
    expected_prevented = sum(txn.amount_minor for txn in stopped_fraud)
    assert impact.unauthorized_minor == expected_unauthorized
    assert impact.prevented_minor == expected_prevented
    # No stopped value leaked into the realized-loss bucket.
    assert impact.unauthorized_minor <= impact.exposure_minor - impact.prevented_minor


def test_combined_configuration_blocked_transactions_are_not_realized_loss(session):
    run = create_run(
        session, scenario=SCENARIO, n_events=EVENTS, seed=SEED, defense_config="COMBINED"
    )
    run_id = run["id"]

    transactions = _transactions(session, run_id)
    blocked = [txn for txn in transactions if txn.status == "BLOCKED"]
    assert blocked, "the combined defense should block at least one scripted attack transfer"

    session.expire_all()
    impact = repo.governance.financial_impact_for_run(session, run_id)
    assert impact is not None

    # A blocked transfer can only ever appear in the prevented bucket, never in the
    # realized (unauthorized) loss the bank actually absorbed.
    expected_unauthorized = sum(
        txn.amount_minor for txn in transactions if txn.status == "SETTLED" and txn.is_fraud == 1
    )
    blocked_value = sum(txn.amount_minor for txn in blocked)
    assert impact.unauthorized_minor == expected_unauthorized
    assert impact.unauthorized_minor + blocked_value <= impact.exposure_minor


def test_combined_configuration_persists_linked_rows_and_open_incidents(session):
    run = create_run(
        session, scenario=SCENARIO, n_events=EVENTS, seed=SEED, defense_config="COMBINED"
    )
    run_id = run["id"]
    assert run["status"] in TERMINAL_STATUSES

    transactions = _transactions(session, run_id)
    detections = _detections(session, run_id)

    # Every stored transaction links to exactly one detection result.
    linked = {detection.transaction_id for detection in detections}
    assert {txn.id for txn in transactions} == linked

    incidents = repo.incidents.incidents_for_run(session, run_id)
    assert incidents
    for incident in incidents:
        assert incident.run_id == run_id
        # Incidents start in the documented initial state.
        assert incident.status == "OPEN"

    # The run row finishes in a terminal status.
    session.expire_all()
    persisted = repo.runs.get_run(session, run_id)
    assert persisted.status in TERMINAL_STATUSES
    assert persisted.generated_events > 0


def test_persisted_money_columns_are_integer_paise_not_floats(session):
    run = create_run(
        session, scenario=SCENARIO, n_events=EVENTS, seed=SEED, defense_config="RULE_BASED"
    )
    run_id = run["id"]

    for txn in _transactions(session, run_id):
        assert isinstance(txn.amount_minor, int) and not isinstance(txn.amount_minor, bool)

    session.expire_all()
    impact = repo.governance.financial_impact_for_run(session, run_id)
    assert impact is not None
    for column in (
        "exposure_minor",
        "unauthorized_minor",
        "prevented_minor",
        "interrupted_minor",
        "disruption_cost_minor",
        "recovery_cost_minor",
        "net_impact_minor",
    ):
        value = getattr(impact, column)
        assert isinstance(value, int) and not isinstance(value, bool), column

    # A blocked incident's defense action also stores integer paise.
    for action in _defense_actions(session, run_id):
        assert isinstance(action.amount_minor, int)
