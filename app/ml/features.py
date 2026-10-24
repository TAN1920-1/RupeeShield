"""Stateful, causal feature engineering.

Design principle
----------------
Every feature for transaction *T* is computed using **only** transactions that
happened strictly *before* T for that account. This is what "causal" means here,
and it is the difference between a model that works in a notebook and one that
would actually work in production.

The same :class:`UserStateStore` class is used:

  (a) offline, replaying historical rows in timestamp order during training, and
  (b) online, updated incrementally as the simulation/API streams transactions.

That removes the offline/online feature mismatch that is one of the most common
real bugs in fraud systems, because there is only one implementation.

Per-account state (all O(1), or O(k) with a small bounded k, so scoring is fast):
  * rolling amount statistics (count, mean, M2) -> Welford's online algorithm
  * timestamps of the last N transactions -> velocity features (10min / 1h / 24h)
  * last known country / device -> consistency checks
  * timestamp of the previous transaction -> recency feature
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta

FEATURE_ORDER = [
    "amount",
    "hour",
    "day_of_week",
    "is_weekend",
    "is_late_night",
    "time_since_prev_txn_min",
    "txns_last_10min",
    "txns_last_1h",
    "txns_last_24h",
    "user_avg_amount",
    "amount_over_user_avg",
    "amount_zscore",
    "is_new_device",
    "is_new_country",
    "is_channel_switch",
]


@dataclass
class UserState:
    n: int = 0
    mean: float = 0.0
    m2: float = 0.0
    last_ts: datetime | None = None
    recent_ts: deque = field(default_factory=lambda: deque(maxlen=200))
    known_devices: set = field(default_factory=set)
    known_countries: set = field(default_factory=set)
    last_channel: str | None = None

    def update_amount(self, amount: float) -> None:
        self.n += 1
        delta = amount - self.mean
        self.mean += delta / self.n
        delta2 = amount - self.mean
        self.m2 += delta * delta2

    @property
    def std(self) -> float:
        if self.n < 2:
            return 0.0
        return math.sqrt(self.m2 / (self.n - 1))


class UserStateStore:
    """In-memory store of per-account behavioural state.

    Deliberately isolated behind this class so it could be swapped for Redis in a
    multi-replica deployment without touching the feature maths at all.
    """

    def __init__(self) -> None:
        self._states: dict[str, UserState] = {}

    def get(self, user_id: str) -> UserState:
        if user_id not in self._states:
            self._states[user_id] = UserState()
        return self._states[user_id]

    def compute_features(
        self,
        user_id: str,
        amount: float,
        ts: datetime,
        channel: str,
        country: str,
        device_id: str,
    ) -> dict:
        """Compute features for an incoming transaction from prior state, then update.

        Order matters: read-then-write. Reading after writing would leak the
        current transaction into its own features.
        """
        state = self.get(user_id)

        time_since_prev_min = (
            (ts - state.last_ts).total_seconds() / 60.0 if state.last_ts else 1440.0
        )
        txns_last_10min = sum(1 for t in state.recent_ts if ts - t <= timedelta(minutes=10))
        txns_last_1h = sum(1 for t in state.recent_ts if ts - t <= timedelta(hours=1))
        txns_last_24h = sum(1 for t in state.recent_ts if ts - t <= timedelta(hours=24))

        user_avg_amount = state.mean if state.n > 0 else amount
        amount_over_avg = amount / user_avg_amount if user_avg_amount > 0 else 1.0
        amount_zscore = (amount - state.mean) / state.std if state.std > 0 else 0.0

        is_new_device = 1 if device_id not in state.known_devices and state.n > 0 else 0
        is_new_country = 1 if country not in state.known_countries and state.n > 0 else 0
        is_channel_switch = 1 if state.last_channel and state.last_channel != channel else 0

        features = {
            "amount": amount,
            "hour": ts.hour,
            "day_of_week": ts.weekday(),
            "is_weekend": int(ts.weekday() >= 5),
            "is_late_night": int(ts.hour <= 5 or ts.hour >= 23),
            "time_since_prev_txn_min": min(time_since_prev_min, 1440.0),
            "txns_last_10min": txns_last_10min,
            "txns_last_1h": txns_last_1h,
            "txns_last_24h": txns_last_24h,
            "user_avg_amount": round(user_avg_amount, 2),
            "amount_over_user_avg": round(amount_over_avg, 3),
            "amount_zscore": round(amount_zscore, 3),
            "is_new_device": is_new_device,
            "is_new_country": is_new_country,
            "is_channel_switch": is_channel_switch,
        }

        # --- state update happens AFTER features are read (causality) ---
        state.update_amount(amount)
        state.recent_ts.append(ts)
        state.known_devices.add(device_id)
        state.known_countries.add(country)
        state.last_channel = channel
        state.last_ts = ts

        return features

    def seed_history(
        self,
        user_id: str,
        amounts: list[float],
        timestamps: list[datetime],
        channel: str | None,
        country: str | None,
        device_id: str | None,
    ) -> None:
        """Replay pre-existing history for an account so its first live
        transaction is not scored against an empty state.

        Used when the simulator attaches to accounts that already have a ledger
        history: without this, every account would look "brand new" and every
        first transaction would carry ``is_new_device``/``is_new_country``.
        """
        state = self.get(user_id)
        for amount, ts in zip(amounts, timestamps):
            state.update_amount(amount)
            state.recent_ts.append(ts)
            state.last_ts = ts
        if device_id:
            state.known_devices.add(device_id)
        if country:
            state.known_countries.add(country)
        state.last_channel = channel

    def snapshot_size(self) -> int:
        return len(self._states)
