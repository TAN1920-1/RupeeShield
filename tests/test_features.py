"""Stateful, causal feature engineering.

The one property that makes these features trustworthy — and the one that is
easiest to break — is causality: every feature for transaction *T* must be
computed from transactions that happened strictly *before* T. A leak of the
current transaction into its own features would make a model look great offline
and fail in production, so it is proven here rather than assumed.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from app.ml.features import FEATURE_ORDER, UserStateStore

BASE_TS = datetime(2025, 4, 1, 10, 0, 0)


def test_feature_order_matches_the_keys_produced_for_a_transaction():
    store = UserStateStore()
    features = store.compute_features("U1", 250.0, BASE_TS, "CARD_PRESENT", "IN", "D1")
    # Exact order, not just the same set: the model consumes a positional vector.
    assert list(features) == FEATURE_ORDER


def test_brand_new_account_gets_the_documented_defaults():
    store = UserStateStore()
    features = store.compute_features("NEW", 500.0, BASE_TS, "UPI_WALLET", "IN", "DX")

    # No prior transaction, so recency defaults to a full day and velocities are zero.
    assert features["time_since_prev_txn_min"] == 1440.0
    assert features["txns_last_10min"] == 0
    assert features["txns_last_1h"] == 0
    assert features["txns_last_24h"] == 0
    # With no history the account's own average is the current amount.
    assert features["user_avg_amount"] == 500.0
    assert features["amount_over_user_avg"] == 1.0
    assert features["amount_zscore"] == 0.0
    # Novelty flags are suppressed on the first transaction: nothing is "new" yet.
    assert features["is_new_device"] == 0
    assert features["is_new_country"] == 0
    assert features["is_channel_switch"] == 0


def test_second_transaction_sees_the_first_one():
    store = UserStateStore()
    store.compute_features("U2", 200.0, BASE_TS, "CARD_PRESENT", "IN", "D1")
    second = store.compute_features(
        "U2", 200.0, BASE_TS + timedelta(minutes=5), "CARD_PRESENT", "IN", "D1"
    )

    assert second["time_since_prev_txn_min"] == 5.0
    assert second["txns_last_10min"] == 1
    assert second["txns_last_1h"] == 1
    assert second["txns_last_24h"] == 1
    assert second["user_avg_amount"] == 200.0
    assert second["amount_over_user_avg"] == 1.0
    # Same device and country as the previous transaction: nothing is novel.
    assert second["is_new_device"] == 0
    assert second["is_new_country"] == 0
    assert second["is_channel_switch"] == 0


def test_velocity_windows_count_only_transactions_inside_the_window():
    store = UserStateStore()
    store.compute_features("U3", 100.0, BASE_TS, "CARD_PRESENT", "IN", "D1")
    store.compute_features("U3", 100.0, BASE_TS + timedelta(minutes=5), "CARD_PRESENT", "IN", "D1")

    # 20 minutes after the first: both earlier transactions fall outside 10 min.
    at_20 = store.compute_features(
        "U3", 100.0, BASE_TS + timedelta(minutes=20), "CARD_PRESENT", "IN", "D1"
    )
    assert at_20["txns_last_10min"] == 0
    assert at_20["txns_last_1h"] == 2
    assert at_20["txns_last_24h"] == 2

    at_50 = store.compute_features(
        "U3", 100.0, BASE_TS + timedelta(minutes=50), "CARD_PRESENT", "IN", "D1"
    )
    assert at_50["txns_last_10min"] == 0
    assert at_50["txns_last_1h"] == 3
    assert at_50["txns_last_24h"] == 3

    # 23 hours later only the 24h window still reaches back to the first transaction.
    at_23h = store.compute_features(
        "U3", 100.0, BASE_TS + timedelta(hours=23), "CARD_PRESENT", "IN", "D1"
    )
    assert at_23h["txns_last_10min"] == 0
    assert at_23h["txns_last_1h"] == 0
    assert at_23h["txns_last_24h"] == 4


def test_device_and_country_novelty_flip_to_known_after_being_seen():
    store = UserStateStore()
    store.compute_features("U4", 100.0, BASE_TS, "CARD_PRESENT", "IN", "DEV-A")

    known = store.compute_features(
        "U4", 100.0, BASE_TS + timedelta(minutes=1), "CARD_PRESENT", "IN", "DEV-A"
    )
    assert known["is_new_device"] == 0
    assert known["is_new_country"] == 0

    new_device = store.compute_features(
        "U4", 100.0, BASE_TS + timedelta(minutes=2), "CARD_PRESENT", "IN", "DEV-B"
    )
    assert new_device["is_new_device"] == 1
    assert new_device["is_new_country"] == 0

    new_country = store.compute_features(
        "U4", 100.0, BASE_TS + timedelta(minutes=3), "CARD_PRESENT", "US", "DEV-A"
    )
    assert new_country["is_new_device"] == 0
    assert new_country["is_new_country"] == 1


def test_channel_switch_is_one_when_the_channel_changes():
    store = UserStateStore()
    store.compute_features("U5", 100.0, BASE_TS, "CARD_PRESENT", "IN", "D1")
    same = store.compute_features(
        "U5", 100.0, BASE_TS + timedelta(minutes=1), "CARD_PRESENT", "IN", "D1"
    )
    assert same["is_channel_switch"] == 0

    switched = store.compute_features(
        "U5", 100.0, BASE_TS + timedelta(minutes=2), "UPI_WALLET", "IN", "D1"
    )
    assert switched["is_channel_switch"] == 1


def test_running_baseline_uses_only_previous_transactions():
    store = UserStateStore()
    store.compute_features("U6", 100.0, BASE_TS, "CARD_PRESENT", "IN", "D1")

    # An extreme current amount must not leak into its own baseline features.
    extreme = store.compute_features(
        "U6", 100_000.0, BASE_TS + timedelta(hours=3), "CARD_PRESENT", "IN", "D1"
    )
    assert extreme["user_avg_amount"] == 100.0
    assert extreme["amount_over_user_avg"] == round(100_000.0 / 100.0, 3)

    # ...but it does change the baseline the *next* transaction is measured against.
    following = store.compute_features(
        "U6", 100.0, BASE_TS + timedelta(hours=6), "CARD_PRESENT", "IN", "D1"
    )
    assert following["user_avg_amount"] == round((100.0 + 100_000.0) / 2, 2)


def test_cumulative_transaction_count_increases_monotonically_per_account():
    store = UserStateStore()
    counts = []
    for step in range(5):
        store.compute_features(
            "U7", 10.0, BASE_TS + timedelta(minutes=step), "CARD_PRESENT", "IN", "D1"
        )
        counts.append(store.get("U7").n)

    assert counts == [1, 2, 3, 4, 5]
    assert all(later > earlier for earlier, later in zip(counts, counts[1:]))

    # A second account has its own independent count.
    store.compute_features("U8", 10.0, BASE_TS, "CARD_PRESENT", "IN", "D1")
    assert store.get("U8").n == 1
    assert store.get("U7").n == 5
