"""Synthetic account roster for one simulation run.

Each run gets its **own** accounts, created from the run's seed. That is what makes
the Defense Comparison Lab fair: when the same scenario and seed are run under three
different defense configurations, all three start from identical balances with
identical customers. If accounts were shared across runs, the first configuration
would change the starting state of the second and the comparison would be
meaningless.

Account labels are neutral by design (``Account 0007``, never "fraudster"). An
account is only ever *flagged for investigation*; the platform never asserts that an
account belongs to a criminal.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

CHANNELS = ["CARD_PRESENT", "CARD_NOT_PRESENT", "UPI_WALLET", "WIRE_TRANSFER"]
COUNTRIES = ["IN", "US", "GB", "AE", "SG", "NG", "RU", "BR"]
HOME_COUNTRY_WEIGHTS = [0.60, 0.12, 0.07, 0.06, 0.06, 0.04, 0.03, 0.02]

#: The per-account average transaction amount is drawn from the *same* distribution
#: the ML dataset generator uses (``app.ml.synthetic_data.make_users``).
#:
#: This is not cosmetic. ``amount`` is a raw model input, so a simulator whose
#: amounts are an order of magnitude larger than the training data would make every
#: simulated transaction look like an extreme outlier: both models would score
#: legitimate and fraudulent traffic at ~1.0 and the ML comparison would be a scale
#: artefact rather than a measurement. Keeping one amount model for the whole
#: synthetic bank is what makes the numbers in the Defense Comparison Lab meaningful.
AVG_AMOUNT_SHAPE = 2.2
AVG_AMOUNT_SCALE = 45.0
AVG_AMOUNT_OFFSET = 5.0

ROLE_VICTIM = "VICTIM"
ROLE_BENEFICIARY = "BENEFICIARY"
ROLE_BASELINE = "BASELINE"


@dataclass(frozen=True)
class PlannedAccount:
    """An account as designed, before it is written to the database."""

    index: int
    label: str
    home_country: str
    preferred_channel: str
    avg_txn_amount_minor: int
    account_age_days: int
    opening_balance_minor: int
    role: str = ROLE_BASELINE


def build_roster(rng: np.random.Generator, n_accounts: int, victim_count: int) -> list[PlannedAccount]:
    """Create ``n_accounts`` accounts, the first ``victim_count`` designated as targets.

    The victim designation is part of the *scenario script*, chosen from the seed, so
    it is identical for every defense configuration run with that seed.
    """
    if n_accounts < 4:
        raise ValueError("A simulation run needs at least 4 accounts to be meaningful.")

    countries = rng.choice(COUNTRIES, size=n_accounts, p=HOME_COUNTRY_WEIGHTS)
    channels = rng.choice(CHANNELS, size=n_accounts, p=[0.32, 0.28, 0.28, 0.12])
    # Same amount distribution as the ML dataset generator; stored in paise.
    averages = rng.gamma(AVG_AMOUNT_SHAPE, AVG_AMOUNT_SCALE, size=n_accounts) + AVG_AMOUNT_OFFSET
    averages_minor = np.round(averages * 100).astype(np.int64)
    ages = rng.integers(30, 2500, size=n_accounts)
    idle_cash = rng.uniform(60.0, 180.0, size=n_accounts)

    roster: list[PlannedAccount] = []
    for index in range(n_accounts):
        average = int(averages_minor[index])
        if index < victim_count:
            role = ROLE_VICTIM
        elif index < victim_count * 2:
            role = ROLE_BENEFICIARY
        else:
            role = ROLE_BASELINE
        roster.append(
            PlannedAccount(
                index=index,
                label=f"Account {index:04d}",
                home_country=str(countries[index]),
                preferred_channel=str(channels[index]),
                avg_txn_amount_minor=average,
                account_age_days=int(ages[index]),
                # Funded well above the largest designed attack so balances stay
                # non-negative even when a victim is drained across the scenario.
                opening_balance_minor=int(average * idle_cash[index]),
                role=role,
            )
        )
    return roster


def account_id(run_id: str, index: int) -> str:
    """Run-scoped account identifier (fits the 24-character column)."""
    return f"{run_id}-A{index:03d}"


def account_row(run_id: str, account: PlannedAccount) -> dict:
    """Shape a planned account into a ``Account`` ORM row payload."""
    return {
        "id": account_id(run_id, account.index),
        "label": account.label,
        "home_country": account.home_country,
        "preferred_channel": account.preferred_channel,
        "avg_txn_amount_minor": account.avg_txn_amount_minor,
        "account_age_days": account.account_age_days,
        "opening_balance_minor": account.opening_balance_minor,
        "balance_minor": account.opening_balance_minor,
        "risk_status": "NORMAL",
        "is_synthetic": True,
    }


def seed_history_for(
    rng: np.random.Generator,
    account: PlannedAccount,
    history_length: int = 18,
) -> list[tuple[float, int]]:
    """Generate plausible prior activity for feature warm-up (never written to the ledger).

    Without this, every account would look brand new on its first simulated
    transaction and ``is_new_device`` / ``is_new_country`` would fire for everyone,
    which would make the defenses look better than they are. The returned
    ``(days_ago, amount_minor)`` pairs are replayed into the in-memory feature state
    only; they are not rows in ``transactions``, so run statistics stay exact.
    """
    amounts = np.maximum(
        1.0, rng.normal(loc=account.avg_txn_amount_minor, scale=account.avg_txn_amount_minor * 0.3, size=history_length)
    )
    days_ago = np.sort(rng.uniform(2.0, 60.0, size=history_length))[::-1]
    return [(float(days_ago[i]), int(round(amounts[i]))) for i in range(history_length)]
