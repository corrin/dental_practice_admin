"""Risks 2, 3 and 5: read-only questions against production Principle.

    uv run python -m scripts.spikes.principle_reads methods 2026-09-01 2026-10-01
    uv run python -m scripts.spikes.principle_reads candidates 2026-04-01

`methods` totals payments per method per NZ day of entry (`createdAt`); the API carries no
appointment date for payments keyed in Principle. `candidates` times the reads the page would
make to list patients who owe money. Both save their rows beside the app's database and print
only counts, totals and timings.
"""
from __future__ import annotations

import argparse
import asyncio
import collections
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from scripts.spikes.common import (
    accept_invoices,
    amount_paid,
    payment_method,
    raw_rows,
    save,
    settings,
    transaction_key,
)

from dental_practice_admin.config import Environment
from dental_practice_admin.principle import CallError, PrincipleClient, PrincipleError

NZ = ZoneInfo("Pacific/Auckland")


def day(stamp: str) -> str:
    """The NZ calendar day of a Principle UTC timestamp."""
    return datetime.fromisoformat(stamp).astimezone(NZ).date().isoformat()


def nz_midnight(day: str) -> str:
    """The start of an NZ calendar day, with the offset in force that day (+12 or +13)."""
    return datetime.fromisoformat(day).replace(tzinfo=NZ).isoformat()


async def methods(config: Any, start: str, end: str) -> None:
    """Payments per method per day; the Principle half of 'do card days add up'."""
    rows = await raw_rows(config, "/v1/transactions", {
        "createdFrom": nz_midnight(start), "createdTo": nz_midnight(end)})
    unique = {transaction_key(r): r for r in rows}
    print("rows", len(rows), "unique (id, invoice)", len(unique),
          "repeats", len(rows) - len(unique))
    kinds = collections.Counter((payment_method(r), r["provider"], r["type"], r["status"])
                                for r in unique.values())
    for kind, n in kinds.most_common():
        print(" ", n, kind)
    per_day: dict[str, collections.Counter[str]] = collections.defaultdict(collections.Counter)
    for r in unique.values():
        if r["type"] == "payment" and r["status"] == "complete":
            per_day[day(r["createdAt"])][payment_method(r)] += round(r["amount"] * 100)
    for d in sorted(per_day):
        print(" ", d, {m: c / 100 for m, c in sorted(per_day[d].items())})
    print("extendedData keys", collections.Counter(
        k for r in unique.values() for k in (r.get("extendedData") or {})))
    print("saved", save(config, "methods", list(unique.values())))


async def candidates(config: Any, since: str) -> None:
    """Time listing unpaid invoices practice-wide, then reading them per patient."""
    accept_invoices()
    async with PrincipleClient(config) as client:
        began = time.perf_counter()
        invoices = [r async for r in client.rows(
            "listInvoicesByDateRange", query={"createdFrom": nz_midnight(since)})]
        listed = time.perf_counter() - began
        unpaid = [r for r in invoices if r["status"] == "issued"]
        owing: dict[str, float] = collections.defaultdict(float)
        for r in unpaid:
            owing[r["patientId"]] += r["total"] - amount_paid(r)
        print(f"listInvoicesByDateRange since {since}: {len(invoices)} invoices in {listed:.1f}s;",
              f"{len(unpaid)} issued, {len(owing)} patients, owing {sum(owing.values()):.2f}")
        print("statuses", collections.Counter(r["status"] for r in invoices))
        timings: list[float] = []
        refused: collections.Counter[str] = collections.Counter()
        for patient in list(owing)[:15]:
            began = time.perf_counter()
            try:
                await client.get("listInvoices", path_params={"patientId": patient},
                                 query={"status": "issued"})
                timings.append(time.perf_counter() - began)
            except (CallError, PrincipleError) as error:
                refused[type(error).__name__ + " " + str(error)] += 1
        if timings:
            mean = sum(timings) / len(timings)
            print(f"per-patient listInvoices: {len(timings)} ok, mean {mean:.2f}s,",
                  f"max {max(timings):.2f}s")
        print("refused", dict(refused))
    for days in (1, 7):
        since_update = (date.today() - timedelta(days=days)).isoformat()
        began = time.perf_counter()
        changed = await raw_rows(config, "/v1/invoices",
                                 {"updatedFrom": nz_midnight(since_update)})
        print(f"updatedFrom {days} day(s): {len(changed)} invoices in",
              f"{time.perf_counter() - began:.1f}s")
    print("saved", save(config, "candidates", {"invoices": invoices, "timings": timings}))


async def main() -> None:
    """Run one read-only question against production."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    commands = parser.add_subparsers(dest="command", required=True)
    m = commands.add_parser("methods")
    m.add_argument("start")
    m.add_argument("end")
    c = commands.add_parser("candidates")
    c.add_argument("since")
    args = parser.parse_args()
    config = settings(args.env_file, Environment.PRODUCTION)
    if args.command == "methods":
        await methods(config, args.start, args.end)
    else:
        await candidates(config, args.since)


if __name__ == "__main__":
    asyncio.run(main())
