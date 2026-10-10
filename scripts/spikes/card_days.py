"""Risks 2 and 5: check batch deposits against Principle's payments for their channel.

    uv run python -m scripts.spikes.card_days

Reads the latest saved `akahu-deposits` and `methods` outputs and runs the plan's clearing
queue: deposits in bank order claim unclaimed payments of their channel, trying one takings
day, then a run of consecutive days, then any combination of payments, including payments
recorded up to `AFTER` days after the money arrived. What is left is
reported with its difference, and whether one payment under the other card method explains it.
Prints amounts and company names only.
"""
from __future__ import annotations

import argparse
import collections
import re
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from scripts.spikes.common import day, latest, payment_method, settings

from dental_practice_admin.config import Environment

# Deposit line pattern → the Principle methods it settles.
CHANNELS = {
    "Smartpay": (re.compile(r"SmartpaySett"), {"Credit Card"}),
    "Paymark": (re.compile(r"PAYMARK"), {"EFTPOS"}),
    "Southern Cross": (re.compile(r"SouthernCros"), {"Southern Cross"}),
    "ACC": (re.compile(r"ACC Claims"), {"ACC Payment", "acc"}),
}
OTHER_CARD = {"Smartpay": "EFTPOS", "Paymark": "Credit Card"}
WINDOW = 14
AFTER = 10


def cents(amount: float) -> int:
    """Dollars to integer cents, so sums compare exactly."""
    return round(amount * 100)


def subsets(items: list[dict[str, Any]], target: int) -> list[list[dict[str, Any]]]:
    """Every combination of items summing to target; at most two, enough to see ambiguity."""
    found: list[list[dict[str, Any]]] = []

    def walk(i: int, left: int, chosen: list[dict[str, Any]]) -> None:
        if len(found) > 1 or left < 0:
            return
        if left == 0 and chosen:
            found.append(list(chosen))
            return
        for j in range(i, len(items)):
            walk(j + 1, left - items[j]["cents"], [*chosen, items[j]])

    walk(0, target, [])
    return found


def claim(queue: list[dict[str, Any]], banked: date, target: int
          ) -> tuple[str, list[dict[str, Any]]]:
    """How a deposit is explained by unclaimed payments, and which ones it claims."""
    open_items = [i for i in queue if not i["claimed"]
                  and (banked - timedelta(days=WINDOW)).isoformat() <= i["day"]
                  <= (banked + timedelta(days=AFTER)).isoformat()]
    days = sorted({i["day"] for i in open_items if i["day"] <= banked.isoformat()},
                  reverse=True)
    by_day = {d: [i for i in open_items if i["day"] == d] for d in days}
    for d in days:
        if sum(i["cents"] for i in by_day[d]) == target:
            return f"one day, {(banked - date.fromisoformat(d)).days} before", by_day[d]
    for length in (2, 3, 4):
        for start in range(len(days) - length + 1):
            run = [i for d in days[start:start + length] for i in by_day[d]]
            if sum(i["cents"] for i in run) == target:
                return f"{length} consecutive days", run
    if len(open_items) <= 25:
        found = subsets(open_items, target)
        if len(found) == 1:
            late = any(i["day"] > banked.isoformat() for i in found[0])
            return ("a combination, some recorded after the deposit" if late
                    else "a combination of payments"), found[0]
        if found:
            return "several combinations; none chosen", []
    return "unexplained", []


def main() -> None:
    """Run each channel's queue over the deposits and summarise how each was explained."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    config = settings(parser.parse_args().env_file, Environment.PRODUCTION)
    deposits = latest(config, "akahu-deposits")
    payments = [p for p in latest(config, "methods")
                if p["type"] == "payment" and p["status"] == "complete"]
    first_taking = min(day(p["createdAt"]) for p in payments)
    last_taking = max(day(p["createdAt"]) for p in payments)

    for channel, (pattern, methods) in CHANNELS.items():
        queue: list[dict[str, Any]] = [
            {"day": day(p["createdAt"]), "cents": cents(p["amount"]), "claimed": False}
                 for p in payments if payment_method(p) in methods]
        other = [{"day": day(p["createdAt"]), "cents": cents(p["amount"])}
                 for p in payments if payment_method(p) == OTHER_CARD.get(channel)]
        outcomes: collections.Counter[str] = collections.Counter()
        notes = []
        for d in sorted((d for d in deposits if pattern.search(d["description"])),
                        key=lambda d: d["date"]):
            banked = date.fromisoformat(day(d["date"]))
            if not first_taking < banked.isoformat() <= last_taking:
                continue
            how, claimed = claim(queue, banked, cents(d["amount"]))
            outcomes[how] += 1
            for item in claimed:
                item["claimed"] = True
            if how == "unexplained":
                recent = [i for i in queue if not i["claimed"]
                          and (banked - timedelta(days=3)).isoformat() <= i["day"]
                          <= banked.isoformat()]
                gaps: dict[str, int] = collections.Counter()
                for i in recent:
                    gaps[i["day"]] += i["cents"]
                nearest: tuple[str | None, int] = min(
                    gaps.items(), key=lambda g: abs(g[1] - cents(d["amount"])),
                    default=(None, 0))
                diff = cents(d["amount"]) - nearest[1]
                swapped = any(o["cents"] == abs(diff) and o["day"] == nearest[0] for o in other)
                notes.append(f"{banked} {d['amount']:>9.2f} nearest day {nearest[0]} "
                             f"{nearest[1] / 100:>9.2f} diff {diff / 100:+.2f}"
                             + ("  (one payment under the other card method)" if swapped else ""))
        settled_by = (date.fromisoformat(last_taking) - timedelta(days=3)).isoformat()
        stale: collections.Counter[str] = collections.Counter()
        for i in queue:
            if not i["claimed"] and i["day"] <= settled_by:
                stale[i["day"]] += i["cents"]
        print(f"{channel}: {dict(outcomes)}; unclaimed by {settled_by}:",
              {d: c / 100 for d, c in sorted(stale.items())})
        for note in notes:
            print("   ", note)


if __name__ == "__main__":
    main()
