"""In-application documentation payload.

The documentation lives next to the code it describes so it cannot drift out of
date the way a wiki does. Everything here is generated from the same constants the
running system uses:

* the financial-impact formulas come from :mod:`app.services.financial_impact`, so
  the numbers a reader sees in the UI are the ones documented here;
* the detection and correlation rules come from the live configuration objects;
* the dataset provenance comes from :mod:`app.ml.dataset`.

The ``commands`` list contains only commands that exist in this repository. If a
command is not verifiable from the repository contents it does not belong here —
documenting a command that fails is worse than documenting nothing.
"""
from __future__ import annotations

from app.core.config import settings
from app.ml.dataset import DATASET_SOURCES
from app.services.financial_impact import formula_reference
from app.services.settings_service import SECRET_NOTES, thresholds_as_dict

TITLE = "RupeeShield Documentation"

ETHICS = [
    "This platform simulates a bank. It never connects to a real bank, a real payment "
    "network or any third-party system, and it makes no real payments.",
    "Every attack is a locally generated scenario script. No capability is provided to "
    "attack, scan, probe or degrade any external system.",
    "All accounts, transactions, devices and security events are synthetic. No real "
    "personal or financial data is stored, processed or displayed.",
    "Accounts are never described as criminal. The strongest label used anywhere is "
    "'flagged for investigation' or 'elevated simulated risk'.",
    "Ground-truth fraud labels come from the scenario generator, so every precision and "
    "recall figure in the UI is a measurement against a known answer, not a claim about "
    "real-world performance.",
    "Results are educational. They illustrate how detection and correlation trade off "
    "against false positives; they are not a statement about any production fraud system.",
]

TROUBLESHOOTING = [
    {
        "problem": "The API starts but /api/overview returns zeros everywhere.",
        "resolution": "No simulation has been run yet. Open Attack Simulator and run a scenario; "
                      "every Overview metric is computed from stored rows, so it is genuinely empty "
                      "until data exists.",
    },
    {
        "problem": "The models are reported as unavailable and every decision is rule-only.",
        "resolution": "Train them locally: run `python scripts/generate_dataset.py` and then "
                      "`python -m app.ml.train`. Nothing is downloaded and no key is required.",
    },
    {
        "problem": "Live Monitoring shows nothing while a run is in progress.",
        "resolution": "The feed reads persisted rows. Create the run with mode = 'stream' so events "
                      "are written as they are generated; a batch run writes everything before it "
                      "returns.",
    },
    {
        "problem": "The combined defense configuration is reported as RULE_BASED in a comparison.",
        "resolution": "The trained ML artefacts are missing, so the ML components are unavailable. "
                      "The comparison says so in its notes rather than silently reporting a "
                      "rules-only result as a combined one.",
    },
    {
        "problem": "Replay reports a non-zero divergence count.",
        "resolution": "The recomputed decision differs from the recorded one, which means the "
                      "thresholds or the model artefacts changed after the incident was recorded. "
                      "Replay never edits the original incident, so the recorded history is intact.",
    },
    {
        "problem": "The frontend cannot reach the API.",
        "resolution": "Start the backend on port 8000 (`uvicorn app.main:app --port 8000`). The Vite "
                      "dev server proxies /api and /health to 127.0.0.1:8000.",
    },
    {
        "problem": "Requests start returning 429.",
        "resolution": "The in-process sliding-window limiter is deliberately low. Wait for the "
                      "window to pass or raise RUPESHIELD_RATE_LIMIT_REQUESTS_PER_MINUTE.",
    },
]

COMMANDS = [
    {"label": "Install Python dependencies", "command": "pip install -r requirements.txt"},
    {
        "label": "Generate the synthetic ML dataset",
        "command": "python scripts/generate_dataset.py --n-users 800 --n-transactions 40000",
    },
    {"label": "Train the detection models", "command": "python -m app.ml.train"},
    {"label": "Start the backend", "command": "uvicorn app.main:app --reload --port 8000"},
    {"label": "Install frontend dependencies", "command": "npm --prefix frontend install"},
    {"label": "Start the dashboard", "command": "npm --prefix frontend run dev"},
    {"label": "Run the backend test suite", "command": "pytest"},
    {"label": "Run the frontend test suite", "command": "npm --prefix frontend run test"},
]


def _threshold_lines() -> list[str]:
    """The live threshold values, rendered as sentences for the Settings page."""
    values = thresholds_as_dict()
    return [
        f"Block threshold: {values['block_threshold']} — at or above this risk score a transaction "
        "is stopped before settlement.",
        f"Flag threshold: {values['flag_threshold']} — at or above this score (and below the block "
        "threshold) a transaction is held for human review.",
        f"Velocity rules: more than {values['rule_velocity_10min']} transactions in 10 minutes or "
        f"{values['rule_velocity_1h']} in an hour raises the rule score.",
        f"Amount rules: a z-score above {values['rule_amount_zscore']} or an amount above "
        f"{values['rule_amount_over_avg']}x the account average raises the rule score.",
        f"Correlation window: security events are correlated with transactions over "
        f"{values['correlation_lookback_minutes']} minutes.",
        f"Failed-login burst: {values['failed_login_burst']} failures inside "
        f"{values['failed_login_window_minutes']} minutes triggers a correlation rule.",
        f"New device + high value: a first-seen device spending at or above "
        f"{values['new_device_high_value_minor']} paise triggers a correlation rule.",
        f"Coordinated fan-out: transfers to {values['coordinated_fanout_beneficiaries']} or more "
        "distinct new beneficiaries inside the window triggers a correlation rule.",
    ]


def build_documentation() -> dict:
    """Assemble the ``DocumentationOut`` payload."""
    thresholds = thresholds_as_dict()
    block = thresholds["block_threshold"]
    flag = thresholds["flag_threshold"]

    sections = [
        {
            "key": "overview",
            "title": "What RupeeShield is",
            "body": (
                "RupeeShield is an educational cyber range for financial fraud analytics. It runs a "
                "simulated bank: a ledger, account balances, authentication and device telemetry, and "
                "four scripted attack scenarios. A detection pipeline scores the resulting traffic, a "
                "separate correlation component relates transactions to security events, and a "
                "financial-impact engine reports what the simulated event cost and what the defense "
                "saved."
            ),
            "bullets": [
                "Everything runs locally. No paid API, no cloud service, no API key.",
                "The simulated bank is the only target; the platform cannot act outside itself.",
                "All four scenarios are deterministic: the same scenario, seed and event count "
                "produce the same script.",
            ],
        },
        {
            "key": "research_question",
            "title": "The question this project tests",
            "body": (
                "Does combining transaction anomaly detection with security-event correlation reduce "
                "simulated financial losses compared with transaction-only detection? The Defense "
                "Comparison Lab is the experiment: one scenario script, three defense "
                "configurations, measured net simulated impact. The hypothesis is not treated as "
                "proven. The comparison reports whichever configuration actually measured best, "
                "including the case where the extra detection cost more in false positives than it "
                "saved."
            ),
            "bullets": [
                "One scenario and one seed is one data point; several seeds are needed before any "
                "difference can be read as a general result.",
                "Precision and recall are measured against the scenario generator's own labels, never "
                "against the detector's own output.",
            ],
        },
        {
            "key": "safety",
            "title": "Scope and safety limitations",
            "body": (
                "This is a simulation. It contains no offensive capability, targets nothing outside "
                "its own database, and stores no real personal or financial data. It must not be "
                "presented as a production-grade banking security system: it has no authentication, "
                "no multi-tenant isolation and no hardened deployment."
            ),
            "bullets": [
                "No real bank, payment network or third-party endpoint is contacted at any point.",
                "No arbitrary command execution: the only shell commands in the project are the ones "
                "a developer runs by hand.",
                "No secrets are required, so nothing secret can leak through configuration.",
                "Audit logging records simulation actions and defensive decisions for review.",
            ],
        },
        {
            "key": "detection",
            "title": "Detection engine",
            "body": (
                "A hybrid score blends a supervised classifier that learns known fraud typologies "
                "with an unsupervised anomaly detector that flags statistically unusual behaviour, "
                "plus auditable rule heuristics. Features are computed causally — one state store "
                "serves both training replay and serving — so there is no train/serve skew."
            ),
            "bullets": [
                f"A score at or above {block} blocks before settlement; at or above {flag} it is held "
                "for review; below that it settles.",
                "The ML components are optional. With no trained artefacts the engine runs rule-only "
                "and reports the ML components as null rather than inventing a score.",
                "Metrics reported are precision, recall, F1, PR-AUC, the false-positive rate and "
                "inference latency. Accuracy is deliberately not a headline number because fraud is "
                "heavily imbalanced.",
            ],
        },
        {
            "key": "correlation",
            "title": "Security-event correlation",
            "body": (
                "A separate, independently testable component relates simulated authentication, "
                "device and payment-integrity events to transactions. It boosts risk only; it can "
                "never lower it. Every alert names the rule that produced it and the events that "
                "support it, and duplicate alerts on the same account and rule inside a short window "
                "are collapsed."
            ),
            "bullets": [
                "Correlation is an additive boost over the transaction-only score; changing the "
                "defense configuration changes whether it runs at all.",
                "Rules are configurable from Settings, so the effect of a threshold change is "
                "measurable rather than asserted.",
            ],
        },
        {
            "key": "financial_impact",
            "title": "Financial impact and its assumptions",
            "body": (
                "The impact engine separates value that moved from value that did not. Transaction "
                "value is not loss. A transaction blocked before settlement is not a realized loss, "
                "whether it was fraudulent (prevented) or legitimate (interrupted). Costs that are "
                "not transfers are modelled as documented rates. All arithmetic is integer paise."
            ),
            "bullets": [
                "Exposure, unauthorized, prevented and interrupted are disjoint sets, so nothing is "
                "counted twice.",
                "The exact formulas and every assumption are listed below and are also returned with "
                "each impact payload.",
            ],
        },
        {
            "key": "interpreting_metrics",
            "title": "How to read the metrics",
            "body": (
                "Each evaluation block states which data produced it: train, validation, test or "
                "simulation. A precision or recall figure computed on a slice containing only one "
                "class is returned as null with an explanation instead of a misleading zero. The "
                "false-positive rate matters as much as recall: a defense that stops everything "
                "prevents every loss while interrupting ordinary business, and the interrupted value "
                "is costed in the impact model."
            ),
            "bullets": [
                "The dataset is synthetic, so model metrics are an upper bound rather than a "
                "real-world estimate.",
                "Simulation metrics are computed on scenario data, which is a different population "
                "from the ML training split.",
            ],
        },
        {
            "key": "reproducibility",
            "title": "Reproducibility",
            "body": (
                "Scenarios are deterministic in (scenario, seed, event count). The scenario plan's "
                "SHA-256 fingerprint is stored on every run, so a comparison can prove that its "
                "configurations received identical inputs. Identifiers are generated with a "
                "cryptographically secure source and are therefore not reproducible; the data the "
                "identifiers point at is."
            ),
            "bullets": [
                "Replay recomputes decisions read-only and reports where they diverge from the "
                "recorded ones.",
                "Re-running a comparison with the same scenario and seed reproduces the same "
                "fingerprint.",
            ],
        },
        {
            "key": "thresholds",
            "title": "Current configuration",
            "body": (
                "These are the values the running process is actually using, read from the same "
                "settings object the detection engine reads. Changing them in Settings takes effect "
                "immediately and is persisted for the next startup."
            ),
            "bullets": _threshold_lines(),
        },
        {
            "key": "secrets",
            "title": "Configuration and secrets",
            "body": (
                "Configuration is non-sensitive and lives in environment variables and "
                "app/core/config.py. Runtime threshold overrides are written to a gitignored JSON file."
            ),
            "bullets": list(SECRET_NOTES),
        },
    ]

    return {
        "title": TITLE,
        "version": settings.app_version,
        "sections": sections,
        "formulas": formula_reference(),
        "dataset_provenance": {
            "active_source": "synthetic",
            "summary": (
                "The ML dataset is generated locally by this project. PaySim is supported as an "
                "optional bring-your-own file but is not bundled, and it contains no cybersecurity "
                "labels — those come from our own scenarios."
            ),
            "sources": DATASET_SOURCES,
        },
        "ethics": ETHICS,
        "troubleshooting": TROUBLESHOOTING,
        "commands": COMMANDS,
    }


__all__ = ["COMMANDS", "ETHICS", "TITLE", "TROUBLESHOOTING", "build_documentation"]
