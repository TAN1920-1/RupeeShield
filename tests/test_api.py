"""End-to-end HTTP contract tests for the RupeeShield backend (``app/api``).

The suite is written against the *real* response shapes: every route module under
``app/api/routes`` is exercised, and the assertions check values and cross-cutting
invariants rather than merely "did it return 200".

Two invariants recur, and are the reason several tests exist at all:

* **Money is exact.** Every ``*_minor`` field is an integer number of paise and its
  major-unit twin is exactly ``minor / 100``. A silent float regression would
  corrupt the financial-impact figures in the UI without anything else failing.
* **The platform never accuses.** An account is only ever labelled
  ``NORMAL`` / ``ELEVATED_SIMULATED_RISK`` / ``FLAGGED_FOR_INVESTIGATION``; the API
  must never describe a person or account as a criminal.

Determinism: every run created here passes an explicit ``seed``, and nothing relies
on wall-clock ordering. The database is the temporary one set up by
``tests/conftest.py``; tests that mutate state (settings, incident lifecycle,
deletes) either use the autouse threshold reset or create their own short run so the
shared fixtures stay intact.
"""
from __future__ import annotations

import pytest

from app.security.ratelimit import rate_limiter

#: Fields of a financial-impact payload that exist as an exact minor-unit integer
#: and a display twin in major units.
MONEY_FIELDS = (
    "exposure",
    "unauthorized",
    "prevented",
    "interrupted",
    "disruption_cost",
    "recovery_cost",
    "net_impact",
)

#: The only labels an account may ever carry.
ALLOWED_RISK_LABELS = {"NORMAL", "ELEVATED_SIMULATED_RISK", "FLAGGED_FOR_INVESTIGATION"}

ALLOWED_DECISIONS = {"ALLOW", "FLAG", "BLOCK"}

@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """Keep the sliding-window limiter from tripping between tests.

    The limiter is per-process and shared by the session-scoped client; clearing it
    makes the suite independent of how many requests earlier tests happened to make.
    """
    rate_limiter.reset()
    yield


def _create_run(client, *, scenario="ACCOUNT_TAKEOVER", n_events=10, seed=1234,
                defense_config="COMBINED", mode="batch"):
    response = client.post(
        "/api/simulation/runs",
        json={
            "scenario": scenario,
            "n_events": n_events,
            "seed": seed,
            "defense_config": defense_config,
            "mode": mode,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _assert_minor_twin(major, minor, name: str = "amount") -> None:
    """A display value in major units must be exactly its minor-unit twin / 100."""
    assert isinstance(minor, int) and not isinstance(minor, bool), name
    assert abs(major - minor / 100.0) < 1e-9, name


def _assert_money_invariants(impact: dict) -> None:
    """Every monetary field is exact paise and the display twin is minor / 100."""
    for name in MONEY_FIELDS:
        _assert_minor_twin(impact[name], impact[f"{name}_minor"], name)
    # The six "bucket"/cost fields describe money that exists, so they are never
    # negative. ``net_impact`` is signed by design (negative means the defense saved
    # more than the event cost) and is checked by the formula below instead.
    for name in ("exposure", "unauthorized", "prevented", "interrupted",
                 "disruption_cost", "recovery_cost"):
        assert impact[f"{name}_minor"] >= 0, name
    # Nothing is counted twice: the stopped buckets are disjoint subsets of exposure.
    assert (
        impact["unauthorized_minor"]
        + impact["prevented_minor"]
        + impact["interrupted_minor"]
        <= impact["exposure_minor"]
    )
    # net_impact = unauthorized + disruption + recovery - prevented
    assert impact["net_impact_minor"] == (
        impact["unauthorized_minor"]
        + impact["disruption_cost_minor"]
        + impact["recovery_cost_minor"]
        - impact["prevented_minor"]
    )


# ---------------------------------------------------------------------------
# health
# ---------------------------------------------------------------------------
def test_health_reports_status_models_and_accounts(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert isinstance(body["models_loaded"], bool)
    assert isinstance(body["tracked_accounts"], int)
    assert body["tracked_accounts"] >= 0


def test_health_model_flag_matches_model_info(client):
    health = client.get("/api/health").json()
    info = client.get("/api/ml/model-info").json()
    assert health["models_loaded"] == info["models_available"]


# ---------------------------------------------------------------------------
# overview
# ---------------------------------------------------------------------------
def test_overview_stats_are_internally_consistent(client, completed_run):
    response = client.get("/api/overview")
    assert response.status_code == 200
    body = response.json()
    stats = body["stats"]

    assert stats["suspicious_transactions"] == (
        stats["blocked_transactions"] + stats["flagged_transactions"]
    )
    assert abs(stats["total_value"] - stats["total_value_minor"] / 100.0) < 1e-9
    assert abs(stats["value_at_risk"] - stats["value_at_risk_minor"] / 100.0) < 1e-9
    assert abs(stats["loss_prevented"] - stats["loss_prevented_minor"] / 100.0) < 1e-9
    assert abs(stats["realized_loss"] - stats["realized_loss_minor"] / 100.0) < 1e-9
    assert stats["total_simulated_transactions"] > 0, "the shared run populated the ledger"

    valid_sources = {"observed_dataset", "simulation", "model", "estimate"}
    assert stats["provenance"], "every headline metric must state where it came from"
    for provenance in stats["provenance"].values():
        assert provenance["source"] in valid_sources

    charts = body["charts"]
    assert isinstance(charts["volume_over_time"], list)
    assert isinstance(charts["decisions_breakdown"], list)
    assert isinstance(body["recent_events"], list)
    assert isinstance(body["empty_states"], list)


# ---------------------------------------------------------------------------
# scenarios
# ---------------------------------------------------------------------------
def test_scenarios_catalogue_lists_the_four_scenarios(client):
    response = client.get("/api/scenarios")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 4
    assert body["returned"] == len(body["items"]) == 4
    keys = {item["key"] for item in body["items"]}
    assert keys == {
        "ACCOUNT_TAKEOVER",
        "CREDENTIAL_STUFFING",
        "PAYMENT_MANIPULATION",
        "SUSPICIOUS_TRANSFER",
    }
    for item in body["items"]:
        assert item["name"]
        assert item["objective"]
        assert item["risk_level"] in {"LOW", "MEDIUM", "HIGH"}
        assert isinstance(item["signature"], list) and item["signature"]


# ---------------------------------------------------------------------------
# simulation
# ---------------------------------------------------------------------------
def test_simulation_run_list_contains_shared_run(client, completed_run):
    response = client.get("/api/simulation/runs")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] >= 1
    assert completed_run["id"] in {item["id"] for item in body["items"]}


def test_simulation_run_list_scenario_filter(client, completed_run):
    scenario = completed_run["scenario"]
    response = client.get("/api/simulation/runs", params={"scenario": scenario})
    assert response.status_code == 200
    body = response.json()
    assert body["items"], "the shared scenario must have at least one run"
    assert all(item["scenario"] == scenario for item in body["items"])


def test_simulation_run_list_rejects_unknown_scenario(client):
    response = client.get("/api/simulation/runs", params={"scenario": "NOT_A_SCENARIO"})
    assert response.status_code == 422


def test_simulation_run_detail_financial_impact_invariants(client, completed_run):
    response = client.get(f"/api/simulation/runs/{completed_run['id']}")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == completed_run["id"]
    assert body["status"] == "COMPLETED"
    assert body["generated_events"] >= 1
    assert body["detection_summary"], "a COMPLETED run reports its detection summary"
    assert body["assumptions"], "the impact assumptions travel with the numbers"

    impact = body["financial_impact"]
    assert impact is not None
    _assert_money_invariants(impact)


def test_simulation_unknown_run_is_404(client):
    response = client.get("/api/simulation/runs/RUN-does-not-exist")
    assert response.status_code == 404
    assert response.json()["code"] == "not_found"


def test_simulation_invalid_scenario_body_is_422(client):
    response = client.post(
        "/api/simulation/runs",
        json={"scenario": "BOGUS", "n_events": 10, "seed": 1, "defense_config": "COMBINED"},
    )
    assert response.status_code == 422


def test_simulation_invalid_defense_config_body_is_422(client):
    response = client.post(
        "/api/simulation/runs",
        json={"scenario": "ACCOUNT_TAKEOVER", "n_events": 10, "seed": 1,
              "defense_config": "TURBO"},
    )
    assert response.status_code == 422


@pytest.mark.parametrize("n_events", [4, 1501])
def test_simulation_n_events_out_of_range_is_422(client, n_events):
    response = client.post(
        "/api/simulation/runs",
        json={"scenario": "ACCOUNT_TAKEOVER", "n_events": n_events, "seed": 1,
              "defense_config": "COMBINED"},
    )
    assert response.status_code == 422


def test_simulation_stop_is_idempotent_on_finished_run(client, completed_run):
    response = client.post(f"/api/simulation/runs/{completed_run['id']}/stop")
    assert response.status_code == 200
    assert response.json()["status"] == "COMPLETED"


def test_simulation_stop_unknown_run_is_404(client):
    response = client.post("/api/simulation/runs/RUN-nope/stop")
    assert response.status_code == 404


def test_simulation_delete_removes_the_run(client):
    run = _create_run(client, n_events=5, seed=99, defense_config="NONE")
    response = client.delete(f"/api/simulation/runs/{run['id']}")
    assert response.status_code == 200
    assert run["id"] in response.json()["message"]
    assert client.get(f"/api/simulation/runs/{run['id']}").status_code == 404


# ---------------------------------------------------------------------------
# monitoring
# ---------------------------------------------------------------------------
def test_monitoring_feed_is_ordered_and_tagged(client, completed_run):
    response = client.get("/api/monitoring/events", params={"run_id": completed_run["id"]})
    assert response.status_code == 200
    body = response.json()
    assert body["run_id"] == completed_run["id"]
    events = body["events"]
    assert events, "the completed run wrote monitoring rows"

    sequences = [event["sequence"] for event in events]
    assert sequences == sorted(sequences)
    assert len(set(sequences)) == len(sequences), "sequence numbers are unique"
    for event in events:
        assert event["source"] == "simulation"
        assert event["run_id"] == completed_run["id"]
        assert isinstance(event["event_type"], str) and event["event_type"]

    # Transactions carry money and a detector decision; security telemetry (raw
    # event types such as FAILED_LOGIN) never does. Both share the same feed.
    transactions = [event for event in events if event["event_type"] == "TRANSACTION"]
    assert transactions, "the feed interleaves scored transactions"
    for event in transactions:
        assert event["amount_minor"] is not None
        _assert_minor_twin(event["amount"], event["amount_minor"], "amount")
        assert event["decision"] is None or event["decision"] in ALLOWED_DECISIONS
    assert body["total"] >= len(events)


def test_monitoring_since_sequence_cursor_skips_history(client, completed_run):
    all_events = client.get(
        "/api/monitoring/events", params={"run_id": completed_run["id"]}
    ).json()["events"]
    assert len(all_events) >= 2, "need at least two rows to exercise the cursor"
    last = all_events[-1]["sequence"]
    response = client.get(
        "/api/monitoring/events",
        params={"run_id": completed_run["id"], "since_sequence": last},
    )
    assert response.status_code == 200
    remaining = response.json()["events"]
    assert all(event["sequence"] > last for event in remaining)


def test_monitoring_limit_is_respected(client, completed_run):
    response = client.get(
        "/api/monitoring/events", params={"run_id": completed_run["id"], "limit": 3}
    )
    assert response.status_code == 200
    body = response.json()
    assert len(body["events"]) <= 3
    assert body["total"] >= len(body["events"])


def test_monitoring_defaults_to_latest_run(client, completed_run):
    response = client.get("/api/monitoring/events")
    assert response.status_code == 200
    assert response.json()["run_id"] is not None


@pytest.mark.parametrize("limit", [0, 501])
def test_monitoring_limit_out_of_range_is_422(client, completed_run, limit):
    response = client.get(
        "/api/monitoring/events", params={"run_id": completed_run["id"], "limit": limit}
    )
    assert response.status_code == 422


def test_monitoring_stream_is_bounded_sse_for_completed_run(client, completed_run):
    """A stream for an already-finished run must emit its rows and then stop.

    ``event_stream`` returns as soon as a poll finds the run in a terminal status
    with no new rows, so this test terminates after one poll interval rather than
    hanging on a live feed.
    """
    response = client.get("/api/monitoring/stream", params={"run_id": completed_run["id"]})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    body = response.text
    assert body.startswith(": rupeeshield stream open")
    assert "event: complete" in body


# ---------------------------------------------------------------------------
# transactions
# ---------------------------------------------------------------------------
def test_transactions_page_shape(client, completed_run):
    response = client.get("/api/transactions", params={"run_id": completed_run["id"]})
    assert response.status_code == 200
    body = response.json()
    assert body["total"] > 0
    assert body["returned"] == len(body["items"])
    for item in body["items"]:
        assert item["run_id"] == completed_run["id"]
        _assert_minor_twin(item["amount"], item["amount_minor"], "amount")
        assert item["amount_minor"] >= 0
        assert item["decision"] in ALLOWED_DECISIONS
        # The ground-truth label is a 0/1 flag in the simulators, not a JSON boolean:
        # the frontend contract declares ``is_fraud: number | null``.
        assert item["is_fraud"] in (0, 1)


def test_transactions_run_filter_returns_only_that_run(client, completed_run):
    response = client.get("/api/transactions", params={"run_id": completed_run["id"]})
    assert response.status_code == 200
    assert all(item["run_id"] == completed_run["id"] for item in response.json()["items"])


def test_transactions_pagination_is_a_consistent_prefix(client, completed_run):
    run_id = completed_run["id"]
    big = client.get("/api/transactions", params={"run_id": run_id, "limit": 8}).json()
    small = client.get("/api/transactions", params={"run_id": run_id, "limit": 3}).json()
    assert small["returned"] <= 3
    big_ids = [item["id"] for item in big["items"]]
    small_ids = [item["id"] for item in small["items"]]
    assert small_ids == big_ids[: len(small_ids)]


def test_transactions_decision_filter(client, completed_run):
    response = client.get(
        "/api/transactions", params={"run_id": completed_run["id"], "decision": "BLOCK"}
    )
    assert response.status_code == 200
    assert all(item["decision"] == "BLOCK" for item in response.json()["items"])


def test_transactions_detail_matches_list_id(client, completed_run):
    listed = client.get("/api/transactions", params={"run_id": completed_run["id"]}).json()
    transaction_id = listed["items"][0]["id"]
    response = client.get(f"/api/transactions/{transaction_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == transaction_id
    assert "explanation" in body and "neighborhood" in body and "security_events" in body


def test_transactions_unknown_id_is_404(client):
    assert client.get("/api/transactions/TXN-does-not-exist").status_code == 404


@pytest.mark.parametrize(
    "params",
    [{"status": "NOT_A_STATUS"}, {"decision": "MAYBE"}, {"scenario": "NOPE"},
     {"min_amount": -1}],
)
def test_transactions_invalid_filters_are_422(client, params):
    assert client.get("/api/transactions", params=params).status_code == 422


# ---------------------------------------------------------------------------
# accounts
# ---------------------------------------------------------------------------
def test_accounts_list_matches_health_count(client):
    response = client.get("/api/accounts")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == client.get("/api/health").json()["tracked_accounts"]
    assert body["returned"] == len(body["items"]) == min(body["total"], body["limit"])


def test_accounts_risk_status_filter(client):
    response = client.get("/api/accounts", params={"risk_status": "NORMAL"})
    assert response.status_code == 200
    assert all(item["risk_status"] == "NORMAL" for item in response.json()["items"])


def test_accounts_invalid_risk_status_is_422(client):
    assert client.get("/api/accounts", params={"risk_status": "GUILTY"}).status_code == 422


def test_account_detail_and_events(client):
    account_id = client.get("/api/accounts").json()["items"][0]["id"]
    detail = client.get(f"/api/accounts/{account_id}")
    assert detail.status_code == 200
    assert detail.json()["id"] == account_id
    assert detail.json()["risk_status"] in ALLOWED_RISK_LABELS

    events = client.get(f"/api/accounts/{account_id}/events")
    assert events.status_code == 200
    body = events.json()
    assert body["account"]["id"] == account_id
    assert body["observations"], "observations are phrased as leads and always present"


def test_accounts_unknown_id_is_404(client):
    assert client.get("/api/accounts/ACC-nope").status_code == 404


# ---------------------------------------------------------------------------
# network
# ---------------------------------------------------------------------------
def test_network_graph_invariants(client, completed_run):
    response = client.get("/api/network/graph", params={"run_id": completed_run["id"]})
    assert response.status_code == 200
    body = response.json()
    nodes = body["nodes"]
    edges = body["edges"]
    assert nodes, "the shared run transfers between simulated accounts"
    assert edges, "the shared run has at least one directed edge"

    node_ids = {node["id"] for node in nodes}
    assert all(node["risk_status"] in ALLOWED_RISK_LABELS for node in nodes)

    edge_ids = {edge["id"] for edge in edges}
    suspicious_ids = {edge["id"] for edge in edges if edge["suspicious"]}
    assert suspicious_ids <= edge_ids, "suspicious edges are a subset of all edges"

    for edge in edges:
        assert edge["source"] in node_ids and edge["target"] in node_ids
        # A suspicious edge is exactly one whose detector flagged/blocked a transfer.
        if edge["suspicious"]:
            assert edge["max_risk_score"] is not None
        else:
            assert edge["max_risk_score"] is None

    assert isinstance(body["truncated"], bool)
    assert body["node_limit"] >= 1
    assert body["legend"]


def test_network_graph_incident_scope(client, completed_run):
    incidents = client.get(
        "/api/incidents", params={"scenario": completed_run["scenario"]}
    ).json()["items"]
    assert incidents, "the shared run opened incidents"
    incident_id = incidents[0]["id"]
    response = client.get("/api/network/graph", params={"incident_id": incident_id})
    assert response.status_code == 200
    assert response.json()["incident_id"] == incident_id


def test_network_graph_unknown_incident_is_404(client):
    assert client.get("/api/network/graph", params={"incident_id": "INC-nope"}).status_code == 404


def test_network_graph_raw_text_never_uses_accusatory_words(client, completed_run):
    response = client.get("/api/network/graph", params={"run_id": completed_run["id"]})
    assert response.status_code == 200
    lowered = response.text.lower()
    assert "criminal" not in lowered
    assert "fraudster" not in lowered


def test_network_graph_node_labels_never_accuse(client, completed_run):
    """The node data itself never describes an account with an accusatory word."""
    response = client.get("/api/network/graph", params={"run_id": completed_run["id"]})
    for node in response.json()["nodes"]:
        assert "criminal" not in node["label"].lower()
        assert "fraudster" not in node["label"].lower()


# ---------------------------------------------------------------------------
# incidents
# ---------------------------------------------------------------------------
def _first_incident_id(client, completed_run) -> str:
    items = client.get(
        "/api/incidents", params={"scenario": completed_run["scenario"]}
    ).json()["items"]
    assert items, "the shared run must open at least one incident"
    return items[0]["id"]


def test_incidents_queue(client, completed_run):
    response = client.get("/api/incidents")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] >= 1
    for item in body["items"]:
        assert item["status"] in {"OPEN", "INVESTIGATING", "CONTAINED", "CLOSED"}
        assert item["severity"]
        assert item["primary_account_id"]


def test_incident_detail(client, completed_run):
    incident_id = _first_incident_id(client, completed_run)
    response = client.get(f"/api/incidents/{incident_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == incident_id
    assert isinstance(body["timeline"], list)
    assert "notes" in body


def test_incident_status_workflow(client, completed_run):
    incident_id = _first_incident_id(client, completed_run)
    for status in ("OPEN", "INVESTIGATING", "CONTAINED", "CLOSED"):
        response = client.patch(
            f"/api/incidents/{incident_id}/status", json={"status": status}
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == status
        if status == "CLOSED":
            assert body["closed_at"] is not None
        else:
            assert body["closed_at"] is None


def test_incident_invalid_status_is_422(client, completed_run):
    incident_id = _first_incident_id(client, completed_run)
    response = client.patch(
        f"/api/incidents/{incident_id}/status", json={"status": "PUNISHED"}
    )
    assert response.status_code == 422


def test_incident_note_is_appended_and_read_back(client, completed_run):
    incident_id = _first_incident_id(client, completed_run)
    note = "Investigator note: reviewed the correlated failed-login burst."
    response = client.post(f"/api/incidents/{incident_id}/notes", json={"note": note})
    assert response.status_code == 200, response.text
    assert note in response.json()["notes"]
    assert note in client.get(f"/api/incidents/{incident_id}").json()["notes"]


def test_incident_replay_is_read_only(client, completed_run):
    incident_id = _first_incident_id(client, completed_run)
    before = client.get(f"/api/incidents/{incident_id}").json()

    response = client.post(f"/api/incidents/{incident_id}/replay")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["incident_id"] == incident_id
    assert body["mutates_history"] is False
    assert isinstance(body["divergence_count"], int) and body["divergence_count"] >= 0
    assert body["steps_replayed"] >= 1
    assert body["steps_replayed"] == len(body["steps"])

    after = client.get(f"/api/incidents/{incident_id}").json()
    assert len(after["timeline"]) == len(before["timeline"]), "replay must not edit history"
    assert after["status"] == before["status"]


def test_incidents_unknown_id_is_404(client):
    assert client.get("/api/incidents/INC-nope").status_code == 404
    assert client.patch("/api/incidents/INC-nope/status", json={"status": "OPEN"}).status_code == 404
    assert client.post("/api/incidents/INC-nope/notes", json={"note": "x"}).status_code == 404
    assert client.post("/api/incidents/INC-nope/replay").status_code == 404


# ---------------------------------------------------------------------------
# defense
# ---------------------------------------------------------------------------
def test_defense_config_catalogue(client):
    response = client.get("/api/defense/configs")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 3
    assert {item["key"] for item in body["items"]} == {"NONE", "RULE_BASED", "COMBINED"}
    for item in body["items"]:
        assert isinstance(item["uses_ml"], bool)
        assert isinstance(item["uses_correlation"], bool)


def test_defense_compare_returns_three_flagged_results(client):
    response = client.post(
        "/api/defense/compare",
        json={"scenario": "ACCOUNT_TAKEOVER", "n_events": 20, "seed": 7},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["same_inputs"] is True
    assert [result["config"] for result in body["results"]] == ["NONE", "RULE_BASED", "COMBINED"]
    assert body["conclusion"] and all(isinstance(line, str) for line in body["conclusion"])
    for result in body["results"]:
        # Each configuration was replayed over the same plan, so the money fields are
        # comparable; the twin/exactness invariant must hold for each of them.
        _assert_minor_twin(result["prevented_value"], result["prevented_value_minor"], "prevented_value")
        _assert_minor_twin(result["realized_loss"], result["realized_loss_minor"], "realized_loss")
        _assert_minor_twin(result["net_impact"], result["net_impact_minor"], "net_impact")
        assert result["prevented_value_minor"] >= 0
        assert result["realized_loss_minor"] >= 0
        assert result["run_id"], "each configuration keeps the run it produced"

    history = client.get("/api/defense/comparisons", params={"limit": 50}).json()
    assert body["id"] in {item["id"] for item in history["items"]}


# ---------------------------------------------------------------------------
# ml
# ---------------------------------------------------------------------------
def test_ml_model_info_matches_artifacts(client):
    response = client.get("/api/ml/model-info")
    assert response.status_code == 200
    body = response.json()
    assert isinstance(body["models_available"], bool)
    assert body["artifacts"], "the artifact list is always reported"
    assert all("exists" in artifact for artifact in body["artifacts"])
    if body["models_available"]:
        assert body["setup_instructions"] == []
    else:
        assert body["setup_instructions"], "missing artifacts come with setup instructions"


def test_ml_evaluation_availability_is_consistent(client):
    info = client.get("/api/ml/model-info").json()
    response = client.get("/api/ml/evaluation")
    assert response.status_code == 200
    body = response.json()
    assert body["available"] == info["models_available"]
    assert body["model_info"]["models_available"] == info["models_available"]
    if body["available"]:
        assert body["metrics"], "an available model reports its metric blocks"
        for block in body["metrics"]:
            assert block["key"] and block["label"] and block["split"]


def test_ml_feature_importance_totals_match(client):
    response = client.get("/api/ml/feature-importance")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == len(body["items"])
    # The classifier is fitted from a numeric matrix, so XGBoost reports gains under
    # its own "f0..fN" column names. If the mapping back to the real feature names
    # regresses, every importance silently becomes 0.0 and the chart renders empty —
    # which is exactly what happened before app/ml/evaluate.py resolved them by
    # position, so assert a ranking exists.
    importances = [entry["importance"] for entry in body["items"]]
    assert len(importances) == body["total"]
    assert max(importances) > 0, "feature importances must not all be zero"
    assert importances == sorted(importances, reverse=True)


def test_ml_training_endpoint_is_disabled(client):
    response = client.post("/api/ml/train", json={})
    assert response.status_code == 403
    assert "RUPESHIELD_ENABLE_API_TRAINING" in response.json()["detail"]


# ---------------------------------------------------------------------------
# settings
# ---------------------------------------------------------------------------
def test_settings_get_returns_thresholds(client):
    response = client.get("/api/settings")
    assert response.status_code == 200
    body = response.json()
    thresholds = body["thresholds"]
    assert 0.0 < thresholds["flag_threshold"] < thresholds["block_threshold"] <= 1.0
    assert body["secret_notes"], "the app states which secrets it does and does not need"
    assert isinstance(body["enable_api_training"], bool)


def test_settings_patch_with_contradictory_thresholds_is_422(client):
    response = client.patch(
        "/api/settings",
        json={"thresholds": {"flag_threshold": 0.95, "block_threshold": 0.90}},
    )
    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"


def test_settings_patch_out_of_range_is_422(client):
    response = client.patch("/api/settings", json={"thresholds": {"block_threshold": 2.0}})
    assert response.status_code == 422


def test_settings_valid_patch_takes_effect(client):
    response = client.patch(
        "/api/settings",
        json={"thresholds": {"block_threshold": 0.77, "flag_threshold": 0.33}},
    )
    assert response.status_code == 200
    updated = response.json()["thresholds"]
    assert updated["block_threshold"] == 0.77
    assert updated["flag_threshold"] == 0.33

    reread = client.get("/api/settings").json()["thresholds"]
    assert reread["block_threshold"] == 0.77
    assert reread["flag_threshold"] == 0.33


# ---------------------------------------------------------------------------
# docs
# ---------------------------------------------------------------------------
def test_docs_payload_is_generated_from_live_configuration(client):
    response = client.get("/api/docs")
    assert response.status_code == 200
    body = response.json()
    assert body["title"]
    assert body["sections"], "documentation sections are present"
    assert body["formulas"], "the financial-impact formulas are documented"
    assert body["ethics"], "the safety/ethics statement is present"
    assert body["commands"]


# ---------------------------------------------------------------------------
# audit
# ---------------------------------------------------------------------------
def test_audit_log_is_newest_first_and_filterable(client, completed_run):
    response = client.get("/api/audit", params={"limit": 50})
    assert response.status_code == 200
    body = response.json()
    assert body["total"] >= 1
    assert {item["action"] for item in body["items"]} >= {"SIMULATION_RUN_CREATED"}

    filtered = client.get("/api/audit", params={"action": "SIMULATION_RUN_CREATED"}).json()
    assert filtered["items"]
    assert all(item["action"] == "SIMULATION_RUN_CREATED" for item in filtered["items"])


# ---------------------------------------------------------------------------
# /score (the root-level direct scoring contract)
# ---------------------------------------------------------------------------
def test_score_happy_path(client):
    response = client.post(
        "/score",
        json={
            "user_id": "SCORE_TEST_USER",
            "amount": 1500.0,
            "channel": "UPI_WALLET",
            "country": "IN",
            "merchant_id": "M1",
            "device_id": "D1",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["transaction_id"].startswith("REQ")
    assert body["decision"] in ALLOWED_DECISIONS
    assert 0.0 <= body["fraud_risk_score"] <= 1.0
    assert body["latency_ms"] >= 0.0
    assert isinstance(body["reasons"], list)
    assert isinstance(body["top_shap_features"], list)


@pytest.mark.parametrize("amount", [0, -100.0])
def test_score_invalid_amount_is_422(client, amount):
    response = client.post(
        "/score",
        json={
            "user_id": "SCORE_TEST_USER",
            "amount": amount,
            "channel": "UPI_WALLET",
            "country": "IN",
            "merchant_id": "M1",
            "device_id": "D1",
        },
    )
    assert response.status_code == 422
