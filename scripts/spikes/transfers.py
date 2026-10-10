"""Risk 4: what identifies a transfer's payer, and is it already recorded in Principle.

    uv run python -m scripts.spikes.transfers

Reads the latest saved `akahu-deposits` and `methods` outputs. Individual deposits are those
no batch channel claims. Prints counts and patterns only, never names or references.
"""
from __future__ import annotations

import argparse
import collections
import re
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from scripts.spikes.card_days import CHANNELS, cents
from scripts.spikes.common import day, latest, payment_method, settings

from dental_practice_admin.config import Environment

LOOKAHEAD = 7


def payer(description: str) -> str:
    """The payer part of an Akahu transfer description, digits removed."""
    return re.sub(r"\s+", " ", re.sub(r"\d", "", description)).strip().upper()


def main() -> None:
    """Count payer repeats and Direct Deposit payments matching each individual deposit."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    config = settings(parser.parse_args().env_file, Environment.PRODUCTION)
    deposits = [d for d in latest(config, "akahu-deposits")
                if not any(p.search(d["description"]) for p, _ in CHANNELS.values())]
    payments = [p for p in latest(config, "methods")
                if p["type"] == "payment" and p["status"] in {"complete", "pending"}]
    last_taking = max(day(p["createdAt"]) for p in payments)
    print("individual deposits", len(deposits))
    print("meta present", collections.Counter(k for d in deposits for k in d["meta"]))
    payers = collections.Counter(payer(d["description"]) for d in deposits)
    print("distinct payers", len(payers),
          "paying more than once", sum(n > 1 for n in payers.values()),
          "deposits from repeat payers", sum(n for n in payers.values() if n > 1))

    direct: list[dict[str, Any]] = [{"day": day(p["createdAt"]), "cents": cents(p["amount"]), "method": payment_method(p),
               "used": False} for p in payments]
    outcomes: collections.Counter[str] = collections.Counter()
    lags: collections.Counter[int] = collections.Counter()
    for d in sorted(deposits, key=lambda d: d["date"]):
        banked = date.fromisoformat(day(d["date"]))
        if banked.isoformat() > (date.fromisoformat(last_taking) - timedelta(days=LOOKAHEAD)
                                 ).isoformat():
            outcomes["too recent to judge"] += 1
            continue
        same = [p for p in direct if not p["used"] and p["cents"] == cents(d["amount"])
                and banked - timedelta(days=LOOKAHEAD) <= date.fromisoformat(p["day"])
                <= banked + timedelta(days=LOOKAHEAD)]
        if not same:
            outcomes["no payment of that amount within a week"] += 1
            continue
        best = min(same, key=lambda p: (p["method"] != "Direct Deposit",
                                        abs((date.fromisoformat(p["day"]) - banked).days)))
        best["used"] = True
        outcomes[f"a {best['method']} payment of that amount"] += 1
        if best["method"] == "Direct Deposit":
            lags[(date.fromisoformat(best["day"]) - banked).days] += 1
    for outcome, n in outcomes.most_common():
        print(" ", n, outcome)
    print("Direct Deposit recorded, days after the bank date", dict(sorted(lags.items())))


if __name__ == "__main__":
    main()
