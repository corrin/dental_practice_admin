"""Phase 0 bank-reconciliation spikes: shared helpers. Removed before the Phase 0 PR merges.

Helpers marked "Phase 1" are shaped to move into `src/` unchanged when the page is built.
"""
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx2 as httpx
import jsonref
from jsonschema import Draft202012Validator

from dental_practice_admin.config import STAGING_API_URL, Environment, Settings
from dental_practice_admin.principle import SPEC

PLAIN_SPEC = jsonref.replace_refs(SPEC, proxies=False, lazy_load=False)


def settings(env_file: Path, environment: Environment) -> Settings:
    """The app's configuration for one environment, from the given .env."""
    options: dict[str, Any] = {"_env_file": env_file, "environment": environment}
    return Settings(**options)


def staging_settings(env_file: Path) -> Settings:
    """Settings that can only address staging; anything that writes starts here.

    `environment` is always what was asked for, so the URL is what proves where calls go.
    """
    config = settings(env_file, Environment.STAGING)
    if config.api_base_url.rstrip("/") != STAGING_API_URL:
        raise SystemExit("Refusing: the API URL is not staging")
    return config


def save(settings: Settings, name: str, data: object) -> Path:
    """Write spike output beside the app's database, never into the checkout."""
    folder = settings.data_dir / "spikes"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{datetime.now(UTC):%Y%m%dT%H%M%S}-{name}.json"
    path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    return path


def accept_invoices() -> None:
    """Let the client validate invoices; Phase 1 fixes this in principle.py.

    AllocationTarget pairs a `discriminator` with inline `oneOf` branches and no mapping, so
    openapi-core resolves "practitioner" to a missing component schema and rejects every
    invoice that has allocations. The `oneOf` alone still validates them.
    """
    from openapi_core import OpenAPI

    from dental_practice_admin import principle

    spec = json.loads(principle.SPEC_BYTES)
    spec["components"]["schemas"]["AllocationTarget"].pop("discriminator")
    principle.VALIDATOR = OpenAPI.from_dict(spec)


async def raw_pages(settings: Settings, path: str, query: dict[str, Any] | None = None
                    ) -> AsyncIterator[dict[str, Any]]:
    """Yield each page's envelope as sent, with no validation or duplicate checks.

    For seeing what Principle returns where the client refuses it. `path` must be passed
    from Python: Git Bash rewrites a `/v1/...` command-line argument into a Windows path.
    """
    sent = {"practiceId": settings.practice_id, "limit": 100, **(query or {})}
    headers = {"X-API-Key": settings.api_key.get_secret_value()}
    async with httpx.AsyncClient(base_url=settings.api_base_url, headers=headers,
                                 timeout=60) as client:
        while True:
            body = (await client.get(path, params=sent)).raise_for_status().json()
            yield body
            cursor = body.get("meta", {}).get("nextOffsetId")
            if not cursor:
                return
            sent["offsetId"] = cursor


async def raw_rows(settings: Settings, path: str, query: dict[str, Any] | None = None
                   ) -> list[dict[str, Any]]:
    """Every row from `raw_pages`, repeats included."""
    return [row async for page in raw_pages(settings, path, query) for row in page["data"]]


def schema_problems(operation: str, body: object) -> list[str]:
    """Where a 200 response breaks the published schema, without the offending values.

    Messages are left out because they quote the value, which can be a patient's details.
    """
    op = next(op for item in PLAIN_SPEC["paths"].values() for op in item.values()
              if isinstance(op, dict) and op.get("operationId") == operation)
    schema = op["responses"]["200"]["content"]["application/json"]["schema"]
    return [f"{'.'.join(str(p) for p in error.absolute_path)}: {error.validator} "
            f"{str(error.validator_value)[:80]}"
            for error in Draft202012Validator(schema).iter_errors(body)]


def payment_method(transaction: dict[str, Any]) -> str:
    """The method staff chose in Principle (Phase 1).

    `provider` is `manual` for every desk payment seen; the method is the practice's own
    manual transaction type, e.g. "EFTPOS", "Credit Card", "Southern Cross".
    """
    kind = (transaction.get("extendedData") or {}).get("transactionType") or {}
    return str(kind.get("name") or transaction["provider"])


def transaction_key(transaction: dict[str, Any]) -> tuple[str, str]:
    """A transaction row's identity (Phase 1).

    One payment split across invoices is listed once per invoice under the same `id`, with
    that invoice's share as `amount`, so `id` alone repeats.
    """
    return transaction["id"], transaction["invoiceId"]


def amount_paid(invoice: dict[str, Any]) -> float:
    """What has been allocated to an invoice so far, in dollars (Phase 1)."""
    return round(sum(float(a["allocatedAmount"]) for t in invoice.get("transactionAllocations", [])
                     for a in t["allocations"]), 2)
