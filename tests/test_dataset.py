"""Dataset loading, schema validation, adaptation and provenance.

These tests use their own CSV fixtures written under ``tmp_path``; the developer's
``data/`` folder is never read or written. The invariants that matter are that a
missing or malformed dataset fails with an *actionable* error rather than silently
inventing rows, and that unlabelled rows are never folded into the fraud rate.
"""
from __future__ import annotations

import pandas as pd
import pytest

from app.ml.dataset import (
    PAYSIM_COLUMNS,
    REQUIRED_COLUMNS,
    DatasetError,
    describe_dataset,
    load_paysim_csv,
    load_transactions_csv,
    validate_schema,
)


def _write_csv(path, columns, rows) -> None:
    pd.DataFrame(rows, columns=columns).to_csv(path, index=False)


def _valid_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "transaction_id": "T1", "user_id": "U1", "timestamp": "2025-01-02T10:00:00",
                "amount": 100.5, "channel": "CARD_NOT_PRESENT", "country": "IN",
                "merchant_id": "M1", "device_id": "D1", "is_fraud": 0, "fraud_type": "NONE",
            },
            {
                "transaction_id": "T2", "user_id": "U1", "timestamp": "2025-01-01T09:00:00",
                "amount": 250.0, "channel": "UPI_WALLET", "country": "IN",
                "merchant_id": "M2", "device_id": "D2", "is_fraud": 1, "fraud_type": "ACCOUNT_TAKEOVER",
            },
        ]
    )


def test_missing_file_raises_actionable_error(tmp_path):
    missing = tmp_path / "does-not-exist.csv"
    with pytest.raises(DatasetError) as excinfo:
        load_transactions_csv(missing)
    message = str(excinfo.value)
    assert str(missing) in message
    # The message must tell the user how to create the dataset.
    assert "generate_dataset.py" in message


def test_schema_validation_reports_every_missing_column():
    incomplete = pd.DataFrame({"transaction_id": ["T1"]})
    with pytest.raises(DatasetError) as excinfo:
        validate_schema(incomplete)

    message = str(excinfo.value)
    missing = [column for column in REQUIRED_COLUMNS if column != "transaction_id"]
    for column in missing:
        assert column in message


def test_valid_csv_loads_parsed_timestamps_sorted_and_drops_non_positive_amounts(tmp_path):
    frame = pd.concat(
        [
            _valid_frame(),
            pd.DataFrame(
                [
                    {
                        "transaction_id": "T0", "user_id": "U1", "timestamp": "2024-12-31T00:00:00",
                        "amount": 0.0, "channel": "CARD_PRESENT", "country": "IN",
                        "merchant_id": "M0", "device_id": "D0", "is_fraud": 0, "fraud_type": "NONE",
                    },
                    {
                        "transaction_id": "TNEG", "user_id": "U1", "timestamp": "2024-12-31T00:00:00",
                        "amount": -50.0, "channel": "CARD_PRESENT", "country": "IN",
                        "merchant_id": "M0", "device_id": "D0", "is_fraud": 0, "fraud_type": "NONE",
                    },
                ]
            ),
        ],
        ignore_index=True,
    )
    path = tmp_path / "transactions.csv"
    frame.to_csv(path, index=False)

    loaded = load_transactions_csv(path)

    assert len(loaded) == 2
    assert pd.api.types.is_datetime64_any_dtype(loaded["timestamp"])
    assert (loaded["amount"] > 0).all()
    # Rows are returned in chronological order.
    assert loaded["timestamp"].is_monotonic_increasing
    assert loaded["timestamp"].iloc[0] == pd.Timestamp("2025-01-01T09:00:00")


def test_empty_csv_is_rejected(tmp_path):
    path = tmp_path / "empty.csv"
    pd.DataFrame(columns=REQUIRED_COLUMNS).to_csv(path, index=False)
    with pytest.raises(DatasetError):
        load_transactions_csv(path)


def test_paysim_adapter_maps_alternate_column_names(tmp_path):
    path = tmp_path / "paysim.csv"
    _write_csv(
        path,
        PAYSIM_COLUMNS,
        [
            {
                "step": 3, "type": "TRANSFER", "amount": 1234.56, "nameOrig": "C1",
                "oldbalanceOrg": 5000.0, "newbalanceOrig": 3765.44, "nameDest": "C2",
                "oldbalanceDest": 0.0, "newbalanceDest": 1234.56, "isFraud": 1, "isFlaggedFraud": 0,
            },
            {
                "step": 1, "type": "PAYMENT", "amount": 10.0, "nameOrig": "C3",
                "oldbalanceOrg": 100.0, "newbalanceOrig": 90.0, "nameDest": "C4",
                "oldbalanceDest": 5.0, "newbalanceDest": 15.0, "isFraud": 0, "isFlaggedFraud": 0,
            },
        ],
    )

    adapted = load_paysim_csv(path)

    for column in REQUIRED_COLUMNS:
        assert column in adapted.columns
    assert adapted["timestamp"].is_monotonic_increasing
    fraud_row = adapted[adapted["is_fraud"] == 1].iloc[0]
    assert fraud_row["fraud_type"] == "PAYSIM_FRAUD"
    assert fraud_row["channel"] == "TRANSFER"
    assert adapted[adapted["is_fraud"] == 0].iloc[0]["fraud_type"] == "NONE"


def test_paysim_adapter_rejects_a_file_missing_required_columns(tmp_path):
    path = tmp_path / "broken.csv"
    _write_csv(path, ["step", "amount"], [{"step": 1, "amount": 5.0}])
    with pytest.raises(DatasetError) as excinfo:
        load_paysim_csv(path)
    message = str(excinfo.value)
    assert "type" in message
    assert "isFraud" in message


def test_describe_dataset_counts_labels_and_marks_unlabelled_rows():
    frame = pd.DataFrame(
        [
            {
                "timestamp": pd.Timestamp("2025-01-01T00:00:00"), "channel": "CARD_PRESENT",
                "is_fraud": 1, "fraud_type": "ACCOUNT_TAKEOVER",
            },
            {
                "timestamp": pd.Timestamp("2025-01-01T01:00:00"), "channel": "UPI_WALLET",
                "is_fraud": 0, "fraud_type": "NONE",
            },
            {
                "timestamp": pd.Timestamp("2025-01-01T02:00:00"), "channel": "UPI_WALLET",
                "is_fraud": 0, "fraud_type": "NONE",
            },
            {
                "timestamp": pd.Timestamp("2025-01-01T03:00:00"), "channel": "WIRE_TRANSFER",
                "is_fraud": float("nan"), "fraud_type": None,
            },
        ]
    )

    info = describe_dataset(frame, source="synthetic")

    assert info.rows == 4
    assert info.labelled_rows == 3
    assert info.fraud_rows == 1
    # The fraud rate is computed over labelled rows only: 1 / 3, not 1 / 4.
    assert info.fraud_rate == pytest.approx(1 / 3, abs=1e-6)
    assert info.start_timestamp == "2025-01-01T00:00:00"
    assert info.end_timestamp == "2025-01-01T03:00:00"
    assert info.channels == sorted(["CARD_PRESENT", "UPI_WALLET", "WIRE_TRANSFER"])
    assert info.fraud_types == {"ACCOUNT_TAKEOVER": 1}
    # Provenance is always attached so a reader can tell simulated from observed.
    assert info.license
    assert info.url
    assert info.limitations
    assert info.has_cyber_labels is False


def test_describe_dataset_handles_a_fully_unlabelled_frame():
    frame = pd.DataFrame(
        [
            {
                "timestamp": pd.Timestamp("2025-01-01T00:00:00"), "channel": "CARD_PRESENT",
                "is_fraud": float("nan"), "fraud_type": None,
            }
        ]
    )
    info = describe_dataset(frame)
    assert info.labelled_rows == 0
    assert info.fraud_rate == 0.0
    assert info.fraud_rows == 0
