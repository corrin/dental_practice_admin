"""Capture what Principle actually returns, so the fake can be checked against it.

The fake computes its answers; these recordings are the oracle that proves those answers
carry the shapes and the refusal wording the real API produces. Nothing here is replayed at
test time -- a stored answer stops being true the moment the state changes.

Run against staging only:

    uv run python scripts/record_principle_wire.py

Two rules this enforces:

  * **Scrubbing happens before anything is written.** The staging tenant is a migrated copy
    of a real practice, so patient names, dates of birth, contact details and addresses are
    PHI. Values are replaced; keys, types and nesting are kept, because shape is all a
    recording is for.
  * **Refusals are captured, never authored.** Each provoked error is saved under
    `tests/recordings/refusals/`, which is where tests/fake/server.py reads them from.

One catalogue drives both this script and the drift check, so what gets written and what
gets verified cannot diverge.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from pydantic import SecretStr

from principle_admin.config import (
    STAGING_API_URL,
    ConfigurationError,
    Environment,
    Settings,
    is_production_host,
)
from principle_admin.principle import PrincipleClient, PrincipleError

RECORDINGS = Path(__file__).resolve().parent.parent / "tests" / "recordings"

# One element is a shape; a hundred contacts are a hundred copies of it.
LIST_ELEMENTS_KEPT = 2

# Replaced before anything reaches disk. Keys, not paths, because the same field name
# carries the same kind of value wherever Principle nests it.
SCRUBBED: dict[str, object] = {
    "name": "Redacted Name",
    "firstName": "Redacted",
    "lastName": "Name",
    "preferredName": "Redacted",
    "dateOfBirth": "1970-01-01",
    "email": "redacted@example.invalid",
    "address": "Redacted address",
    "number": "+6490000000",
    "phoneNumber": "+6490000000",
    "mobileNumber": "+6490000000",
    "notes": "Redacted",
}

# A practice name is business information and the fake needs it to build its own label, so
# it survives. Everything else naming a person does not.
KEPT_AT_PATH = frozenset({"practices.name"})


def scrub(value: Any, path: str = "") -> Any:
    """Replace identifying values, keeping keys, types and nesting intact."""
    if isinstance(value, dict):
        return {
            key: (
                SCRUBBED[key]
                if key in SCRUBBED and f"{path}.{key}".lstrip(".") not in KEPT_AT_PATH
                else scrub(item, f"{path}.{key}".lstrip("."))
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [scrub(item, path) for item in value[:LIST_ELEMENTS_KEPT]]
    return value


def write(name: str, status: int, body: object, *, refusal: bool = False) -> Path:
    """Save one scrubbed recording, citing when it was captured."""
    target = (RECORDINGS / "refusals" if refusal else RECORDINGS) / f"{name}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    document = {
        "capturedAt": datetime.now(tz=UTC).isoformat(),
        "source": "api.staging.principle.dental",
        "status": status,
        "body": scrub(body, path=name),
    }
    target.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return target


def staging_settings() -> Settings:
    """Staging configuration, refusing production and missing credentials alike."""
    settings = Settings(environment=Environment.STAGING, api_base_url=STAGING_API_URL)
    if is_production_host(settings.api_base_url):
        raise ConfigurationError("the recorder addresses staging, never production")
    settings.require_credentials()
    return settings


def window() -> dict[str, str]:
    """A fortnight around today: enough diary to hold a shape, not a bulk export."""
    today = date.today()
    return {
        "from": f"{today - timedelta(days=7)}T00:00:00Z",
        "to": f"{today + timedelta(days=7)}T00:00:00Z",
    }


async def record_successes(client: PrincipleClient, practice_id: str) -> list[Path]:
    """Capture one page of each listing the catalogue describes."""
    written = [write("practices", 200, await client.get("list_practices"))]
    written.append(
        write(
            "practitioners",
            200,
            await client.get(
                "list_practitioners", path_params={"practice_id": practice_id}, query={"limit": 2}
            ),
        )
    )
    written.append(
        write(
            "appointments",
            200,
            await client.get(
                "list_appointments",
                query={"practiceId": practice_id, "limit": 2, **window()},
            ),
        )
    )
    return written


async def record_refusals(settings: Settings, practice_id: str) -> list[Path]:
    """Provoke each error the fake needs to be able to speak, and save what was said."""
    written: list[Path] = []

    no_key = settings.model_copy(update={"api_key": SecretStr("")})
    async with PrincipleClient(no_key) as client:
        written.append(await _provoke(client, "unauthorised", "list_practices"))

    async with PrincipleClient(settings) as client:
        written.append(
            await _provoke(
                client,
                "unknown_offset",
                "list_appointments",
                query={
                    "practiceId": practice_id,
                    "offsetId": "no-such-record",
                    **window(),
                },
            )
        )
    return written


async def _provoke(
    client: PrincipleClient, name: str, call: str, query: dict[str, Any] | None = None
) -> Path:
    try:
        envelope = await client.get(call, query=query)
    except PrincipleError as refused:
        return write(name, refused.status, refused.body, refusal=True)
    raise SystemExit(
        f"{name}: expected {call} to be refused but it answered {json.dumps(envelope)[:200]}; "
        "the fake must not be given a refusal the API does not actually produce"
    )


async def main() -> None:
    """Capture every recording the fake needs, successes and refusals alike."""
    settings = staging_settings()
    print(f"recording from {settings.api_base_url} practice {settings.practice_id}")
    async with PrincipleClient(settings) as client:
        written = await record_successes(client, settings.practice_id)
    written += await record_refusals(settings, settings.practice_id)
    for path in written:
        print(f"  wrote {path.relative_to(Path.cwd())}")


if __name__ == "__main__":
    asyncio.run(main())
