"""Fetch Principle's published specification and record a fingerprint of what we call.

The specification itself is **not** committed. It carries no licence, no terms and no contact,
so redistribution rights are unstated, and it cannot be sublicensed under this project's
AGPL-3.0 in any case. It is fetched to a gitignored path instead.

What is committed is `tests/spec/fingerprint.json`: for each operation in
`dental_practice_admin.principle.CATALOGUE`, the method, the path, the parameters the specification
declares, and the shape of the success response. That is a derived description of a published
interface rather than a copy of the document, it is a few dozen lines instead of six thousand,
and it makes the drift check scoped to what we actually call -- so an unrelated Principle
addition does not raise an alarm that gets ignored within a month.

    uv run python scripts/refresh_spec.py          # fetch, rewrite the fingerprint
    uv run python scripts/refresh_spec.py --check   # fail if the fingerprint is stale

`info.version` is not a change signal: the published specification carried 631 more lines and
four more paths than SMS_Bridge's saved copy while both declared 1.1.0. Drift is detected by
content.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path
from typing import Any

import yaml

from dental_practice_admin.principle import CATALOGUE

SPEC_URL = "https://api.principle.dental/assets/api.yml"

SPEC_DIR = Path(__file__).resolve().parent.parent / "tests" / "spec"
LOCAL_SPEC = SPEC_DIR / "principle-api.yml"
FINGERPRINT = SPEC_DIR / "fingerprint.json"

# Deep enough to reach a nested object's fields (an appointment's `event`), shallow enough that
# a recursive schema cannot run away.
MAX_DEPTH = 4


def fetch(url: str = SPEC_URL) -> str:
    """The published specification, as text. Public; no credentials involved."""
    request = urllib.request.Request(url, headers={"Accept": "text/yaml"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return str(response.read().decode("utf-8"))


def _resolve(spec: dict[str, Any], node: Any) -> Any:
    """Follow one `$ref` into the document."""
    if isinstance(node, dict) and "$ref" in node:
        target: Any = spec
        for step in str(node["$ref"]).removeprefix("#/").split("/"):
            target = target[step]
        return target
    return node


def shape(spec: dict[str, Any], schema: Any, depth: int = 0) -> Any:
    """A schema reduced to its field names and types, with `$ref`s resolved.

    Descriptions, examples and titles are dropped: prose changes constantly and says nothing
    about whether our client still works.
    """
    schema = _resolve(spec, schema)
    if not isinstance(schema, dict) or depth >= MAX_DEPTH:
        return "..."
    if "enum" in schema:
        return {"enum": sorted(str(value) for value in schema["enum"])}
    kind = schema.get("type")
    if kind == "array":
        return [shape(spec, schema.get("items", {}), depth + 1)]
    if kind == "object" or "properties" in schema:
        properties = schema.get("properties") or {}
        return {
            "required": sorted(str(name) for name in schema.get("required", [])),
            "properties": {
                str(name): shape(spec, value, depth + 1)
                for name, value in sorted(properties.items())
            },
        }
    formatted = schema.get("format")
    return f"{kind}/{formatted}" if formatted else str(kind)


def operation_fingerprint(spec: dict[str, Any], method: str, path: str) -> dict[str, Any]:
    """Everything about one documented operation that our client depends on."""
    operation = spec["paths"][path][method.lower()]
    parameters = []
    for raw in operation.get("parameters", []):
        parameter = _resolve(spec, raw)
        parameters.append(
            {
                "name": parameter["name"],
                "in": parameter["in"],
                "required": bool(parameter.get("required", False)),
                "schema": shape(spec, parameter.get("schema", {})),
            }
        )
    success = operation["responses"]["200"]["content"]["application/json"]["schema"]
    return {
        "operationId": operation.get("operationId"),
        "method": method,
        "path": path,
        "parameters": sorted(parameters, key=lambda item: (item["in"], item["name"])),
        "response": shape(spec, success),
    }


def build(spec_text: str) -> dict[str, Any]:
    """The fingerprint for every call in CATALOGUE.

    A catalogue entry whose path is absent from the specification is a hard failure: it means
    we are calling something Principle no longer documents.
    """
    spec = yaml.safe_load(spec_text)
    operations: dict[str, Any] = {}
    for call in CATALOGUE:
        # The catalogue templates paths with our own names; the specification uses its own.
        documented = _documented_path(spec, call.path)
        operations[call.name] = operation_fingerprint(spec, call.method, documented)
    return {
        "source": SPEC_URL,
        "specVersion": spec["info"]["version"],
        "note": (
            "Derived from Principle's published specification: the operations this project"
            " calls, reduced to parameters and response shape. Not a copy of the document."
            " specVersion is recorded but is not a change signal -- drift is detected by"
            " content."
        ),
        "operations": operations,
    }


def _documented_path(spec: dict[str, Any], catalogue_path: str) -> str:
    """Match our path template against the specification's, ignoring parameter names.

    `/v1/practices/{practice_id}/practitioners` here is `/v1/practices/{practiceId}/...` there.
    """
    ours = [
        "{}" if segment.startswith("{") else segment
        for segment in catalogue_path.strip("/").split("/")
    ]
    for candidate in spec["paths"]:
        theirs = [
            "{}" if segment.startswith("{") else segment
            for segment in str(candidate).strip("/").split("/")
        ]
        if ours == theirs:
            return str(candidate)
    raise SystemExit(
        f"{catalogue_path} is not in the published specification; either Principle removed it"
        " or the catalogue has a typo"
    )


def main() -> int:
    """Fetch the specification, then rewrite or verify the fingerprint."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="compare the published specification against the committed fingerprint",
    )
    arguments = parser.parse_args()

    spec_text = fetch()
    SPEC_DIR.mkdir(parents=True, exist_ok=True)
    LOCAL_SPEC.write_text(spec_text, encoding="utf-8")
    current = build(spec_text)
    rendered = json.dumps(current, indent=2, sort_keys=True) + "\n"

    if arguments.check:
        if not FINGERPRINT.exists():
            print(f"{FINGERPRINT} is missing; run without --check", file=sys.stderr)
            return 1
        if FINGERPRINT.read_text(encoding="utf-8") != rendered:
            print(
                "Principle's specification has changed for an operation this project calls.\n"
                "Review the difference, then re-run without --check:\n"
                "  uv run python scripts/refresh_spec.py",
                file=sys.stderr,
            )
            return 1
        print(f"fingerprint matches the published specification ({current['specVersion']})")
        return 0

    FINGERPRINT.write_text(rendered, encoding="utf-8")
    print(f"wrote {FINGERPRINT.relative_to(Path.cwd())} from {SPEC_URL}")
    print(f"  spec version {current['specVersion']}, {len(current['operations'])} operations")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
