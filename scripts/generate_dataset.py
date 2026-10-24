"""Generate the synthetic transaction dataset.

    python scripts/generate_dataset.py --n-users 800 --n-transactions 40000

Writes ``data/transactions.csv`` (the ML dataset) and ``data/users.csv`` (the
synthetic account profiles behind it). Neither file is committed: the generator is
seeded, so anyone can regenerate byte-identical data.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import settings  # noqa: E402
from app.ml.synthetic_data import generate  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate synthetic transactions.")
    parser.add_argument("--n-users", type=int, default=4000)
    parser.add_argument("--n-transactions", type=int, default=250_000)
    parser.add_argument("--fraud-rate", type=float, default=0.012)
    parser.add_argument("--start-date", type=str, default="2025-01-01")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out", type=Path, default=settings.data_dir / "transactions.csv")
    parser.add_argument("--users-out", type=Path, default=settings.data_dir / "users.csv")
    parser.add_argument(
        "--sample", action="store_true",
        help="Small, committed sample used for first-time setup and CI.",
    )
    args = parser.parse_args(argv)

    if args.sample:
        args.n_users = min(args.n_users, 300)
        args.n_transactions = min(args.n_transactions, 12_000)
        args.fraud_rate = max(args.fraud_rate, 0.02)

    transactions, users = generate(
        n_users=args.n_users,
        n_transactions=args.n_transactions,
        fraud_rate=args.fraud_rate,
        start_date=args.start_date,
        seed=args.seed,
    )

    args.out.parent.mkdir(parents=True, exist_ok=True)
    transactions.to_csv(args.out, index=False)
    users.to_csv(args.users_out, index=False)

    print(
        f"Generated {len(transactions):,} transactions "
        f"({int(transactions.is_fraud.sum()):,} labelled fraud, "
        f"{transactions.is_fraud.mean() * 100:.2f}%) for {args.n_users:,} synthetic accounts"
    )
    print(f"  transactions -> {args.out}")
    print(f"  accounts     -> {args.users_out}")
    print(transactions.fraud_type.value_counts().to_string())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
