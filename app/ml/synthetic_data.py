"""Synthetic multi-channel transaction generator.

Why synthetic rather than a Kaggle CSV
--------------------------------------
1. The public fraud datasets (ULB ``creditcard.csv``, IEEE-CIS, PaySim) are
   anonymised or pre-aggregated, so individual columns cannot be explained. A
   generator forces every field and every fraud typology to be *designed*, which
   is exactly the work a fraud analytics team does before any modelling.
2. It is free, licence-clean, tiny on disk, and fully reproducible from a seed.
3. PaySim in particular contains **no cybersecurity labels at all** (no logins,
   no device telemetry). Generating our own scenarios means we never have to
   pretend a public dataset contains attributes it does not.

Four injected typologies, each with a distinct behavioural signature:

``CARD_TESTING``
    many tiny transactions in rapid succession on a new device -- a fraudster
    validating stolen card details before a large purchase.

``ACCOUNT_TAKEOVER``
    a burst of large transactions from a new device *and* a foreign country,
    inconsistent with the account's history.

``MULE_FANOUT``
    one large incoming wire transfer immediately followed by several smaller
    transfers out to previously-unseen beneficiaries.

``MERCHANT_COLLUSION``
    repeated suspiciously round-number charges to a single merchant, sitting just
    under typical manual-review thresholds.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pandas as pd

RNG_SEED = 42
CHANNELS = ["CARD_PRESENT", "CARD_NOT_PRESENT", "UPI_WALLET", "WIRE_TRANSFER"]
COUNTRIES = ["IN", "US", "GB", "AE", "SG", "NG", "RU", "BR"]
HOME_COUNTRY_WEIGHTS = [0.55, 0.15, 0.08, 0.07, 0.06, 0.04, 0.03, 0.02]

TRANSACTION_COLUMNS = [
    "transaction_id", "user_id", "timestamp", "amount", "channel",
    "country", "merchant_id", "device_id", "is_fraud", "fraud_type",
]


def make_users(n_users: int, rng: np.random.Generator) -> pd.DataFrame:
    return pd.DataFrame({
        "user_id": [f"U{100000 + i}" for i in range(n_users)],
        "home_country": rng.choice(COUNTRIES, size=n_users, p=HOME_COUNTRY_WEIGHTS),
        "avg_txn_amount": np.round(rng.gamma(shape=2.2, scale=45, size=n_users) + 5, 2),
        "preferred_channel": rng.choice(CHANNELS, size=n_users, p=[0.35, 0.30, 0.25, 0.10]),
        "account_age_days": rng.integers(1, 2500, size=n_users),
    })


def normal_transaction(user, ts, rng) -> dict:
    amount = max(1.0, rng.normal(loc=user.avg_txn_amount, scale=user.avg_txn_amount * 0.35))
    channel = user.preferred_channel if rng.random() < 0.8 else rng.choice(CHANNELS)
    country = user.home_country if rng.random() < 0.95 else rng.choice(COUNTRIES)
    return {
        "user_id": user.user_id,
        "timestamp": ts,
        "amount": round(float(amount), 2),
        "channel": channel,
        "country": country,
        "merchant_id": f"M{rng.integers(1000, 9999)}",
        "device_id": f"D{abs(hash(user.user_id)) % 5000}",
        "is_fraud": 0,
        "fraud_type": "NONE",
    }


def inject_card_testing(user, ts, rng, txns: list) -> None:
    n = rng.integers(6, 15)
    device = f"D{rng.integers(90000, 99999)}"
    for i in range(n):
        txns.append({
            "user_id": user.user_id,
            "timestamp": ts + timedelta(seconds=int(rng.integers(1, 25)) * i),
            "amount": round(float(rng.uniform(0.5, 3.0)), 2),
            "channel": "CARD_NOT_PRESENT",
            "country": rng.choice(COUNTRIES),
            "merchant_id": f"M{rng.integers(1000, 9999)}",
            "device_id": device,
            "is_fraud": 1,
            "fraud_type": "CARD_TESTING",
        })


def inject_account_takeover(user, ts, rng, txns: list) -> None:
    foreign_country = rng.choice([c for c in COUNTRIES if c != user.home_country])
    device = f"D{rng.integers(90000, 99999)}"
    n = rng.integers(2, 5)
    for i in range(n):
        txns.append({
            "user_id": user.user_id,
            "timestamp": ts + timedelta(minutes=int(rng.integers(1, 20)) * i),
            "amount": round(float(user.avg_txn_amount * rng.uniform(4, 12)), 2),
            "channel": rng.choice(["CARD_NOT_PRESENT", "UPI_WALLET"]),
            "country": foreign_country,
            "merchant_id": f"M{rng.integers(1000, 9999)}",
            "device_id": device,
            "is_fraud": 1,
            "fraud_type": "ACCOUNT_TAKEOVER",
        })


def inject_mule_fanout(user, ts, rng, txns: list) -> None:
    funding_amt = round(float(rng.uniform(2000, 9000)), 2)
    txns.append({
        "user_id": user.user_id, "timestamp": ts, "amount": funding_amt,
        "channel": "WIRE_TRANSFER", "country": user.home_country,
        "merchant_id": "EXTERNAL", "device_id": f"D{abs(hash(user.user_id)) % 5000}",
        "is_fraud": 1, "fraud_type": "MULE_FANOUT",
    })
    remaining = funding_amt
    n_out = int(rng.integers(4, 9))
    for i in range(n_out):
        out_amt = round(remaining / n_out * float(rng.uniform(0.7, 1.3)), 2)
        txns.append({
            "user_id": user.user_id,
            "timestamp": ts + timedelta(minutes=int(rng.integers(2, 10)) * (i + 1)),
            "amount": out_amt, "channel": "UPI_WALLET", "country": user.home_country,
            "merchant_id": f"BENEFICIARY_{rng.integers(10000, 99999)}",
            "device_id": f"D{abs(hash(user.user_id)) % 5000}",
            "is_fraud": 1, "fraud_type": "MULE_FANOUT",
        })


def inject_merchant_collusion(user, ts, rng, txns: list) -> None:
    merchant = f"M{rng.integers(1000, 9999)}"
    n = int(rng.integers(3, 6))
    round_amt = int(rng.choice([499, 999, 1999, 4999]))
    for i in range(n):
        txns.append({
            "user_id": user.user_id,
            "timestamp": ts + timedelta(days=int(rng.integers(1, 10)) * (i + 1)),
            "amount": float(round_amt), "channel": "CARD_NOT_PRESENT",
            "country": user.home_country, "merchant_id": merchant,
            "device_id": f"D{abs(hash(user.user_id)) % 5000}",
            "is_fraud": 1, "fraud_type": "MERCHANT_COLLUSION",
        })


FRAUD_INJECTORS = [
    inject_card_testing,
    inject_account_takeover,
    inject_mule_fanout,
    inject_merchant_collusion,
]


def generate(
    n_users: int = 4000,
    n_transactions: int = 250_000,
    fraud_rate: float = 0.012,
    start_date: str = "2025-01-01",
    seed: int = RNG_SEED,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return ``(transactions, users)``.

    Deterministic for a given ``seed``: the same arguments always produce a
    byte-identical dataset, which is what makes the model metrics reproducible.
    """
    rng = np.random.default_rng(seed)
    users = make_users(n_users, rng)
    start = datetime.fromisoformat(start_date)
    txns: list[dict] = []

    # ~4 transactions per injected fraud event on average.
    n_fraud_events = max(int(n_transactions * fraud_rate / 4), 1)
    n_normal = max(n_transactions - n_fraud_events * 4, 1)

    for _ in range(n_normal):
        user = users.iloc[int(rng.integers(0, n_users))]
        ts = start + timedelta(seconds=int(rng.integers(0, 60 * 60 * 24 * 90)))
        txns.append(normal_transaction(user, ts, rng))

    for _ in range(n_fraud_events):
        user = users.iloc[int(rng.integers(0, n_users))]
        ts = start + timedelta(seconds=int(rng.integers(0, 60 * 60 * 24 * 90)))
        injector = FRAUD_INJECTORS[int(rng.integers(0, len(FRAUD_INJECTORS)))]
        injector(user, ts, rng, txns)

    frame = pd.DataFrame(txns).sort_values("timestamp").reset_index(drop=True)
    frame["transaction_id"] = [f"T{i:08d}" for i in range(len(frame))]
    return frame[TRANSACTION_COLUMNS], users
