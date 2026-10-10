"""Risk 1: record and void payments through Principle's API, on staging only.

    uv run python -m scripts.spikes.principle_payments create PATIENT INVOICE AMOUNT DATE
    uv run python -m scripts.spikes.principle_payments void PATIENT INVOICE TRANSACTION
    uv run python -m scripts.spikes.principle_payments show PATIENT INVOICE

`create --type-name NAME --type-id ID` also sends `extendedData.transactionType`, which the
published request schema does not have, to see whether Principle keeps a payment method.
Each command prints the invoice before and after and saves both beside the app's database.
"""
from __future__ import annotations

import argparse
import asyncio
import uuid
from pathlib import Path
from typing import Any

import httpx2 as httpx
from scripts.spikes.common import accept_invoices, amount_paid, save, staging_settings

from dental_practice_admin.principle import PrincipleClient


async def invoice_state(client: PrincipleClient, patient: str, invoice: str) -> dict[str, Any]:
    """The invoice fields a payment should change, and its transactions."""
    row = await client.get("getInvoice", path_params={"patientId": patient, "invoiceId": invoice})
    transactions = await client.get(
        "listTransactions", path_params={"patientId": patient, "invoiceId": invoice})
    return {
        "status": row["status"], "paidAt": row.get("paidAt"), "total": row["total"],
        "allocated": amount_paid(row),
        "transactionAllocations": row.get("transactionAllocations", []),
        "transactions": [
            {k: t.get(k) for k in ("id", "amount", "status", "provider", "type", "reference",
                                   "description", "createdAt", "updatedAt", "extendedData")}
            for t in transactions["data"]],
    }


async def main() -> None:
    """Run one command and save the invoice before and after."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create")
    create.add_argument("patient")
    create.add_argument("invoice")
    create.add_argument("amount", type=float)
    create.add_argument("date", help="bank date, YYYY-MM-DD")
    create.add_argument("--description", default="Bank transfer")
    create.add_argument("--type-name")
    create.add_argument("--type-id")
    void = commands.add_parser("void")
    void.add_argument("patient")
    void.add_argument("invoice")
    void.add_argument("transaction")
    void.add_argument("--status", default="failed")
    show = commands.add_parser("show")
    show.add_argument("patient")
    show.add_argument("invoice")
    args = parser.parse_args()

    if args.command == "create" and bool(args.type_name) != bool(args.type_id):
        raise SystemExit("--type-name and --type-id go together")
    config = staging_settings(args.env_file)
    accept_invoices()
    record: dict[str, Any] = {"command": vars(args) | {"env_file": str(args.env_file)}}
    async with PrincipleClient(config) as client:
        record["before"] = await invoice_state(client, args.patient, args.invoice)
        if args.command == "create":
            body = {
                "practiceId": config.practice_id, "provider": "manual", "type": "payment",
                "status": "complete", "amount": args.amount,
                "createdAt": f"{args.date}T00:00:00+12:00",
                "reference": f"spike-{uuid.uuid4()}", "description": args.description,
            }
            if args.type_name:
                body["extendedData"] = {"transactionType": {
                    "name": args.type_name, "ref": {"id": args.type_id}}}
                async with httpx.AsyncClient(timeout=60) as raw:
                    response = await raw.post(
                        f"{config.api_base_url}/v1/patients/{args.patient}/invoices/"
                        f"{args.invoice}/transactions",
                        json=body, headers={"X-API-Key": config.api_key.get_secret_value()})
                record["response"] = {"status": response.status_code, "body": response.text}
            else:
                record["response"] = await client.call(
                    "createTransaction", {"patientId": args.patient, "invoiceId": args.invoice,
                                          **body})
            record["request"] = body
        elif args.command == "void":
            record["response"] = await client.call("updateTransaction", {
                "patientId": args.patient, "invoiceId": args.invoice,
                "transactionId": args.transaction, "status": args.status})
        record["after"] = await invoice_state(client, args.patient, args.invoice)
    path = save(config, f"payments-{args.command}", record)
    for key in ("before", "after"):
        state = record[key]
        print(key, state["status"], "paidAt", state["paidAt"], "total", state["total"],
              "allocated", state["allocated"], "transactions", len(state["transactions"]))
    print("response", record.get("response"))
    print("saved", path)


if __name__ == "__main__":
    asyncio.run(main())
