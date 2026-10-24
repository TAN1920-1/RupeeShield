"""Defense Comparison Lab.

The lab answers the project's central question: *does adding security-event
correlation to transaction anomaly detection reduce simulated financial losses?*

It answers it the only way that is worth anything — by running **one** scenario
script three times, changing nothing but the defense:

============================  ============================================================
Configuration                 What runs
============================  ============================================================
``NONE``                      nothing is scored; every transaction settles
``RULE_BASED``                auditable heuristics only
``COMBINED``                  heuristics + supervised ML + anomaly detection + correlation
============================  ============================================================

Fairness is structural, not asserted. The plan is built once from
``(scenario, seed, n_events)`` and its SHA-256 fingerprint is carried into every run's
summary; :func:`compare` then *verifies* that all three runs report the same
fingerprint before claiming ``same_inputs``. The comparison does **not** assume the
combined configuration wins — it reads the net simulated impact of each run and
reports which configuration actually came out ahead, including the case where more
detection made things worse because of false positives.

Ground truth is the synthetic generator's own label (``is_fraud``), never the
detector's output, so precision and recall here are measurements rather than
self-assessment.
"""
from __future__ import annotations

import time

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db import repositories as repo
from app.db.models import DefenseComparisonRecord
from app.security.audit import record_audit
from app.services import simulation_runner
from app.services.financial_impact import minor_units_to_float
from app.services.simulation.scenarios import build_plan
from app.utils.ids import new_id
from app.utils.timeutils import utcnow

logger = get_logger(__name__)

COMPARISON_CONFIGS = ("NONE", "RULE_BASED", "COMBINED")

#: Where the correctness labels come from. Stated in the response so a reader never
#: has to guess whether the metrics were measured or estimated.
LABEL_SOURCE = "Synthetic scenario labels (is_fraud) written by the scenario generator"

STOPPED_STATUSES = ("BLOCKED", "PENDING_REVIEW")


def compare(session: Session, *, scenario: str, n_events: int, seed: int) -> dict:
    """Run one scenario under all three configurations and report what happened."""
    plan = build_plan(scenario, seed=seed, n_events=n_events)
    group_id = new_id("CMP")
    logger.info(
        "Defense comparison %s: %s seed=%s n_events=%s fingerprint=%s",
        group_id,
        scenario,
        seed,
        n_events,
        plan.fingerprint,
    )

    results: list[dict] = []
    notes: list[str] = []
    fingerprints: set[str] = set()

    for config in COMPARISON_CONFIGS:
        started = time.perf_counter()
        detail = simulation_runner.create_run(
            session,
            scenario=scenario,
            n_events=n_events,
            seed=seed,
            defense_config=config,
            mode="batch",
        )
        processing_ms = round((time.perf_counter() - started) * 1000, 2)

        run_id = detail["id"]
        summary = detail.get("summary") or {}
        effective = str(summary.get("defense_config", config))
        if effective != config:
            notes.append(
                f"{config} was requested but the run used {effective}: the trained ML artefacts are "
                "missing. Train them (python scripts/generate_dataset.py; python -m app.ml.train) "
                "to measure the ML contribution."
            )
        if summary.get("fingerprint"):
            fingerprints.add(str(summary["fingerprint"]))

        detections = summary.get("detections", {})
        impact = detail.get("financial_impact") or {}
        prevented_count = _prevented_count(session, run_id)

        config_notes = list(summary.get("notes", []) or [])
        results.append(
            {
                "config": config,
                "name": _config_name(config),
                "detected_events": int(detections.get("detected", 0)),
                "true_positives": int(detections.get("true_positives", 0)),
                "false_positives": int(detections.get("false_positives", 0)),
                "false_negatives": int(detections.get("false_negatives", 0)),
                "precision": detections.get("precision"),
                "recall": detections.get("recall"),
                "false_positive_rate": detections.get("false_positive_rate"),
                "detection_latency_ms": (summary.get("detection_latency") or {}).get("mean_ms"),
                "prevented_count": prevented_count,
                "prevented_value_minor": int(impact.get("prevented_minor", 0)),
                "prevented_value": minor_units_to_float(impact.get("prevented_minor", 0)),
                "realized_loss_minor": int(impact.get("unauthorized_minor", 0)),
                "realized_loss": minor_units_to_float(impact.get("unauthorized_minor", 0)),
                "net_impact_minor": int(impact.get("net_impact_minor", 0)),
                "net_impact": minor_units_to_float(impact.get("net_impact_minor", 0)),
                "processing_ms": processing_ms,
                "run_id": run_id,
                "notes": config_notes
                + (
                    [f"Effective configuration: {effective}."] if effective != config else []
                ),
            }
        )

    same_inputs = len(fingerprints) == 1
    if not same_inputs:  # pragma: no cover - would indicate a bug in plan building
        notes.append(
            "The three runs reported different scenario fingerprints, so the comparison is not "
            "like-for-like. This should not happen; treat the numbers as unreliable."
        )

    conclusion = _conclude(results, plan.fingerprint, same_inputs)

    record = repo.governance.add_comparison(
        session,
        {
            "id": group_id,
            "scenario": scenario,
            "seed": seed,
            "n_events": n_events,
            "label_source": LABEL_SOURCE,
            "same_inputs": same_inputs,
            "results": results,
            "conclusion": conclusion,
        },
    )
    record_audit(
        session,
        action="DEFENSE_COMPARISON_RUN",
        entity_type="defense_comparison",
        entity_id=record.id,
        details={
            "scenario": scenario,
            "seed": seed,
            "n_events": n_events,
            "fingerprint": plan.fingerprint,
            "same_inputs": same_inputs,
        },
    )

    payload = _shape_record(record)
    payload["notes"] = notes
    return payload


def _prevented_count(session: Session, run_id: str) -> int:
    """How many fraudulent transactions the defense stopped before settlement."""
    return sum(
        1
        for row in repo.transactions.transactions_for_run(session, run_id)
        if row.status in STOPPED_STATUSES and row.is_fraud == 1
    )


def _config_name(config: str) -> str:
    return {
        "NONE": "No automated defense",
        "RULE_BASED": "Rule-based defense",
        "COMBINED": "Rule-based + ML + security-event correlation",
    }.get(config, config)


def _conclude(results: list[dict], fingerprint: str, same_inputs: bool) -> list[str]:
    """Describe the outcome of the comparison without assuming a winner.

    The wording is generated from the measured numbers, so a configuration that made
    the simulated outcome *worse* is reported as worse.
    """
    if not results:
        return ["No configuration produced a result, so no comparison can be drawn."]

    lines: list[str] = []
    if same_inputs:
        lines.append(
            f"All three configurations replayed the same scenario script "
            f"(fingerprint {fingerprint}): identical accounts, balances, transactions and attack "
            "parameters. The only variable is the defense."
        )
    else:
        lines.append(
            "The configurations did not run identical inputs, so differences below cannot be "
            "attributed to the defense."
        )

    by_config = {result["config"]: result for result in results}
    baseline = by_config.get("NONE")

    for result in results:
        summary = (
            f"{result['name']}: detected {result['detected_events']} event(s) "
            f"({result['true_positives']} true positive, {result['false_positives']} false positive), "
            f"prevented {result['prevented_count']} fraudulent transaction(s) worth "
            f"{result['prevented_value']:,.2f} INR, and realized a simulated loss of "
            f"{result['realized_loss']:,.2f} INR. Net simulated impact {result['net_impact']:,.2f} INR."
        )
        if baseline and result["config"] != "NONE":
            delta = result["net_impact_minor"] - baseline["net_impact_minor"]
            direction = "better" if delta < 0 else ("worse" if delta > 0 else "identical")
            summary += (
                f" That is {abs(minor_units_to_float(delta)):,.2f} INR {direction} than taking no "
                "automated action."
            )
        lines.append(summary)

    ranked = sorted(results, key=lambda item: item["net_impact_minor"])
    best = ranked[0]
    lines.append(
        f"Lowest net simulated impact: {best['name']} at {best['net_impact']:,.2f} INR."
    )

    guard = by_config.get("COMBINED")
    rule = by_config.get("RULE_BASED")
    if guard and rule:
        if guard["net_impact_minor"] < rule["net_impact_minor"]:
            lines.append(
                "Combining ML and security-event correlation reduced the net simulated impact "
                "relative to rules alone on this scenario, which supports the project's hypothesis "
                "for this scenario and seed."
            )
        elif guard["net_impact_minor"] > rule["net_impact_minor"]:
            lines.append(
                "Rules alone produced a lower net simulated impact than the combined configuration "
                "on this scenario, i.e. the extra detection cost more in disruption than it saved. "
                "This does not support the hypothesis for this scenario and seed."
            )
        else:
            lines.append(
                "Rules alone and the combined configuration produced the same net simulated impact "
                "on this scenario, so no advantage from the extra detection is measurable here."
            )
        if (guard.get("false_positive_rate") or 0.0) > (rule.get("false_positive_rate") or 0.0):
            lines.append(
                f"The combined configuration also had the higher false-positive rate "
                f"({guard.get('false_positive_rate')} vs {rule.get('false_positive_rate')}), so it "
                "stopped more legitimate traffic. Considering repeated runs and further scenarios "
                "is required before drawing a general conclusion."
            )

    lines.append(
        "A single scenario and seed is one data point. Re-run with several seeds before treating "
        "any of these differences as a general result."
    )
    return lines


def _shape_record(record: DefenseComparisonRecord) -> dict:
    return {
        "id": record.id,
        "scenario": record.scenario,
        "scenario_name": _scenario_name(record.scenario),
        "seed": record.seed,
        "n_events": record.n_events,
        "label_source": record.label_source,
        "results": list(record.results or []),
        "same_inputs": bool(record.same_inputs),
        "conclusion": list(record.conclusion or []),
        "created_at": record.created_at,
    }


def _scenario_name(scenario: str) -> str:
    from app.services.simulation import catalog

    return catalog.scenario_name(scenario)


def list_comparison_page(session: Session, *, limit: int, offset: int = 0) -> dict:
    rows = repo.governance.list_comparisons(session, limit=limit)
    items = [_shape_record(row) for row in rows]
    total = len(items) + offset
    return {
        "items": items,
        "total": total,
        "limit": limit,
        "offset": offset,
        "returned": len(items),
    }


__all__ = ["COMPARISON_CONFIGS", "LABEL_SOURCE", "compare", "list_comparison_page"]
