"""Dataset loading, schema validation and provenance.

Two supported sources:

1. **Synthetic (default, always available).** Generated locally by
   ``app.ml.synthetic_data`` / ``scripts/generate_dataset.py``. No download, no
   licence questions, tiny, reproducible from a seed.

2. **PaySim (optional, bring-your-own-file).** A public mobile-money simulation
   dataset. It is *not* bundled: download it yourself, place the CSV anywhere and
   point ``--transactions`` at it. Only its transaction columns are used; it
   contains **no** authentication or device telemetry, so all cybersecurity
   labels used by this project come from our own scenarios, never from PaySim.

Provenance (source URL, licence, limitations) is returned by
:func:`describe_dataset` and surfaced in the UI so a reader can always tell which
numbers are observed data and which are simulated.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path

import pandas as pd

REQUIRED_COLUMNS = [
    "transaction_id", "user_id", "timestamp", "amount", "channel",
    "country", "merchant_id", "device_id", "is_fraud", "fraud_type",
]

PAYSIM_COLUMNS = [
    "step", "type", "amount", "nameOrig", "oldbalanceOrg", "newbalanceOrig",
    "nameDest", "oldbalanceDest", "newbalanceDest", "isFraud", "isFlaggedFraud",
]

DATASET_SOURCES = {
    "synthetic": {
        "name": "RupeeShield synthetic generator",
        "url": "generated locally — see app/ml/synthetic_data.py",
        "license": "MIT (same as this repository)",
        "limitations": [
            "Entirely artificial: distributions are designed, not observed from a real bank.",
            "Fraud typologies are injected deliberately, so a model can learn them almost perfectly.",
            "Metrics on this data are therefore an upper bound, not a real-world performance estimate.",
        ],
        "has_cyber_labels": False,
    },
    "paysim": {
        "name": "PaySim mobile-money simulation dataset",
        "url": "https://www.kaggle.com/datasets/ealaxi/paysim1",
        "license": "CC BY-SA 4.0 (check the Kaggle page for the authoritative terms)",
        "limitations": [
            "Simulated, not real transactions; generated with a multi-agent simulator.",
            "Provides no authentication, device or geo telemetry — no cybersecurity labels.",
            "One step = one hour, so sub-hour velocity features are coarser than in production.",
            "Extremely imbalanced (~0.13% fraudulent rows).",
        ],
        "has_cyber_labels": False,
    },
}


@dataclass
class DatasetInfo:
    """Everything the UI needs to describe where a number came from."""

    source: str
    path: str | None
    rows: int
    fraud_rows: int
    fraud_rate: float
    labelled_rows: int
    start_timestamp: str | None
    end_timestamp: str | None
    channels: list[str] = field(default_factory=list)
    fraud_types: dict[str, int] = field(default_factory=dict)
    license: str = ""
    url: str = ""
    limitations: list[str] = field(default_factory=list)
    has_cyber_labels: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


class DatasetError(RuntimeError):
    """Raised when a dataset is missing, empty or does not match the schema."""


def resolve_dataset_path(configured: Path | None = None) -> Path:
    from app.core.config import settings

    return Path(configured or settings.data_dir / "transactions.csv")


def dataset_exists(configured: Path | None = None) -> bool:
    return resolve_dataset_path(configured).exists()


def validate_schema(frame: pd.DataFrame) -> None:
    missing = [column for column in REQUIRED_COLUMNS if column not in frame.columns]
    if missing:
        raise DatasetError(
            "Dataset is missing required column(s): "
            f"{', '.join(missing)}. Expected schema: {', '.join(REQUIRED_COLUMNS)}."
        )


def load_transactions_csv(path: Path | None = None) -> pd.DataFrame:
    """Load and validate a transaction CSV.

    Raises :class:`DatasetError` with an actionable message rather than failing
    silently or fabricating rows.
    """
    target = resolve_dataset_path(path)
    if not target.exists():
        raise DatasetError(
            f"No dataset found at {target}. Generate one with:\n"
            "    python scripts/generate_dataset.py --n-users 800 --n-transactions 40000"
        )

    frame = pd.read_csv(target)
    if frame.empty:
        raise DatasetError(f"Dataset at {target} is empty (0 rows).")

    validate_schema(frame)
    frame["timestamp"] = pd.to_datetime(frame["timestamp"])
    frame["amount"] = pd.to_numeric(frame["amount"], errors="coerce")
    frame = frame.dropna(subset=["amount", "timestamp", "user_id"])
    frame = frame[frame["amount"] > 0]

    if frame.empty:
        raise DatasetError(f"Dataset at {target} has no valid rows after cleaning.")
    return frame.sort_values("timestamp").reset_index(drop=True)


def load_paysim_csv(path: Path, max_rows: int | None = None) -> pd.DataFrame:
    """Adapt a PaySim CSV to this project's transaction schema.

    PaySim's ``isFraud`` label refers to transaction fraud only. The returned frame
    therefore gets ``fraud_type="PAYSIM_FRAUD"`` (or ``"NONE"``) and no security
    events are invented for it — those come from the simulator.
    """
    target = Path(path)
    if not target.exists():
        raise DatasetError(f"No PaySim file found at {target}.")

    frame = pd.read_csv(target, nrows=max_rows)
    missing = [column for column in PAYSIM_COLUMNS if column not in frame.columns]
    if missing:
        raise DatasetError(
            f"PaySim file is missing column(s): {', '.join(missing)}."
        )

    # One PaySim step == one simulated hour.
    base = pd.Timestamp("2025-01-01")
    adapted = pd.DataFrame({
        "transaction_id": [f"PS{i:09d}" for i in range(len(frame))],
        "user_id": frame["nameOrig"].astype(str),
        "timestamp": base + pd.to_timedelta(frame["step"].astype(int), unit="h"),
        "amount": frame["amount"].astype(float).round(2),
        "channel": frame["type"].astype(str).str.upper(),
        "country": "IN",
        "merchant_id": frame["nameDest"].astype(str),
        "device_id": "PAYSIM_UNKNOWN",
        "is_fraud": frame["isFraud"].astype(int),
        "fraud_type": frame["isFraud"].astype(int).map({1: "PAYSIM_FRAUD", 0: "NONE"}),
    })
    return adapted.sort_values("timestamp").reset_index(drop=True)


def describe_dataset(frame: pd.DataFrame, source: str = "synthetic", path: Path | None = None) -> DatasetInfo:
    """Summarise a dataset, keeping observed statistics clearly separated.

    ``labelled_rows`` matters: rows with no label must never be silently counted
    as legitimate when computing precision/recall.
    """
    provenance = DATASET_SOURCES.get(source, DATASET_SOURCES["synthetic"])
    has_label = frame["is_fraud"].notna()
    labelled = int(has_label.sum())
    fraud_rows = int((frame["is_fraud"] == 1).sum())

    fraud_types = (
        frame.loc[frame["is_fraud"] == 1, "fraud_type"].value_counts().to_dict()
        if labelled else {}
    )

    return DatasetInfo(
        source=source,
        path=str(path) if path else None,
        rows=int(len(frame)),
        fraud_rows=fraud_rows,
        fraud_rate=round(fraud_rows / labelled, 6) if labelled else 0.0,
        labelled_rows=labelled,
        start_timestamp=frame["timestamp"].min().isoformat() if len(frame) else None,
        end_timestamp=frame["timestamp"].max().isoformat() if len(frame) else None,
        channels=sorted(frame["channel"].astype(str).unique().tolist()),
        fraud_types={str(k): int(v) for k, v in fraud_types.items()},
        license=provenance["license"],
        url=provenance["url"],
        limitations=list(provenance["limitations"]),
        has_cyber_labels=bool(provenance["has_cyber_labels"]),
    )
