"""Risk 4: which fields Akahu gives for the practice account's deposits.

    uv run python -m scripts.spikes.akahu_deposits accounts
    uv run python -m scripts.spikes.akahu_deposits deposits ACCOUNT_ID 2026-09-01

Requests follow github.com/corrin/akahu_to_budget (modules/transaction_handler.py,
get_all_akahu), which runs against Akahu daily. Tokens are read from the environment file as
AKAHU_APP_TOKEN and AKAHU_USER_TOKEN. Rows are saved beside the app's database; only names,
counts and field presence are printed.
"""
from __future__ import annotations

import argparse
import asyncio
import collections
from pathlib import Path
from typing import Any

import httpx2 as httpx
from dotenv import dotenv_values
from scripts.spikes.common import save, settings

from dental_practice_admin.config import Environment

AKAHU = "https://api.akahu.io/v1"


def headers(env_file: Path) -> dict[str, str]:
    """Akahu personal-app auth: the user token as bearer, the app token as X-Akahu-ID."""
    env = dotenv_values(env_file)
    if not env.get("AKAHU_APP_TOKEN") or not env.get("AKAHU_USER_TOKEN"):
        raise SystemExit("AKAHU_APP_TOKEN and AKAHU_USER_TOKEN are required")
    return {"Authorization": f"Bearer {env['AKAHU_USER_TOKEN']}",
            "X-Akahu-ID": str(env["AKAHU_APP_TOKEN"])}


async def transactions(client: httpx.AsyncClient, account: str, start: str
                       ) -> list[dict[str, Any]]:
    """Every transaction since `start`, following `cursor.next` until it runs out."""
    rows: list[dict[str, Any]] = []
    params = {"start": f"{start}T00:00:00Z"}
    while True:
        body = (await client.get(f"/accounts/{account}/transactions", params=params)
                ).raise_for_status().json()
        rows += body.get("items", [])
        cursor = (body.get("cursor") or {}).get("next")
        if not body.get("items") or not cursor:
            return rows
        params["cursor"] = cursor


async def main() -> None:
    """List accounts, or dump one account's deposits and report which fields they carry."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("accounts")
    deposits = commands.add_parser("deposits")
    deposits.add_argument("account")
    deposits.add_argument("start", help="YYYY-MM-DD")
    args = parser.parse_args()
    config = settings(args.env_file, Environment.PRODUCTION)
    async with httpx.AsyncClient(base_url=AKAHU, headers=headers(args.env_file),
                                 timeout=60) as client:
        if args.command == "accounts":
            items = (await client.get("/accounts")).raise_for_status().json()["items"]
            for item in items:
                print(item["_id"], item.get("status"), item.get("name"),
                      "refreshed:", item.get("refreshed"))
            return
        rows = await transactions(client, args.account, args.start)
        account = (await client.get(f"/accounts/{args.account}")).raise_for_status().json()
    deposits_in = [r for r in rows if r.get("amount", 0) > 0]
    print("transactions", len(rows), "deposits", len(deposits_in))
    print("refreshed", account.get("item", {}).get("refreshed"))
    print("deposit fields", collections.Counter(k for r in deposits_in for k in r))
    print("meta fields", collections.Counter(k for r in deposits_in for k in (r.get("meta") or {})))
    print("types", collections.Counter(r.get("type") for r in deposits_in))
    print("saved", save(config, "akahu-deposits", deposits_in))


if __name__ == "__main__":
    asyncio.run(main())
