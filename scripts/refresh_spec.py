"""Generate Principle tools and check local, staged, or published interface consistency.

The normalized snapshot holds interface shapes, never examples or patient records.
Only --update and --upstream-check contact Principle; generation is offline.
"""

from __future__ import annotations

import argparse
import copy
import difflib
import hashlib
import json
import re
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator

SPEC_URL = "https://api.principle.dental/assets/api.yml"
ROOT = Path(__file__).resolve().parent.parent
SNAPSHOT = Path("tests/spec/fingerprint.json")
POLICY = Path("tests/spec/compatibility.json")
ARTIFACT = Path("src/dental_practice_admin/generated_principle.json")
FINGERPRINT = ROOT / SNAPSHOT
LOCAL_SPEC = ROOT / "tests/spec/principle-api.yml"
SCHEMA_KEYS = {
    "type",
    "properties",
    "required",
    "items",
    "additionalProperties",
    "enum",
    "const",
    "anyOf",
    "oneOf",
    "allOf",
    "not",
    "format",
    "pattern",
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "minLength",
    "maxLength",
    "minItems",
    "maxItems",
    "uniqueItems",
    "minProperties",
    "maxProperties",
    "multipleOf",
    "propertyNames",
}
ANNOTATIONS = {
    "description",
    "title",
    "example",
    "examples",
    "default",
    "deprecated",
    "readOnly",
    "writeOnly",
    "xml",
    "externalDocs",
    "discriminator",
}


def rendered(value: object) -> str:
    """Canonical representation for generation and comparison."""
    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True) + "\n"


def fetch(url: str = SPEC_URL) -> str:
    """Read the public specification without credentials."""
    request = urllib.request.Request(url, headers={"Accept": "text/yaml"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return str(response.read().decode("utf-8"))


def resolve(spec: dict[str, Any], node: dict[str, Any]) -> dict[str, Any]:
    """Resolve local references, refusing external or cyclic reference chains."""
    visited: set[str] = set()
    while "$ref" in node:
        ref = node["$ref"]
        if not ref.startswith("#/") or ref in visited:
            raise ValueError(f"Unsupported reference: {ref}")
        visited.add(ref)
        target = spec
        for part in ref[2:].split("/"):
            target = target[part.replace("~1", "/").replace("~0", "~")]
        node = {**target, **{k: v for k, v in node.items() if k != "$ref"}}
    return node


def schema(spec: dict[str, Any], raw: dict[str, Any], depth: int = 0) -> dict[str, Any]:
    """Normalize schemas without silently dropping validation keywords."""
    if depth > 30:
        raise ValueError("Recursive or excessively nested schema")
    node = resolve(spec, raw)
    unknown = set(node) - SCHEMA_KEYS - ANNOTATIONS - {"nullable"}
    if unknown:
        raise ValueError(f"Unsupported schema keywords: {sorted(unknown)}")
    result = {k: v for k, v in node.items() if k in SCHEMA_KEYS}
    for key in ("items", "additionalProperties", "not", "propertyNames"):
        if isinstance(result.get(key), dict):
            result[key] = schema(spec, result[key], depth + 1)
    if "properties" in result:
        result["properties"] = {
            k: schema(spec, v, depth + 1) for k, v in result["properties"].items()
        }
    for key in ("anyOf", "oneOf", "allOf"):
        if key in result:
            result[key] = [schema(spec, v, depth + 1) for v in result[key]]
    if node.get("nullable"):
        return {"anyOf": [result, {"type": "null"}]}
    return result


def build(spec_text: str, names: set[str] | None = None) -> dict[str, Any]:
    """Extract scoped business reads plus internal practice discovery."""
    spec = yaml.safe_load(spec_text)
    operations = {}
    for path, item in spec["paths"].items():
        if "get" not in item or path.startswith(("/v1/oauth", "/v1/webhooks")):
            continue
        op = item["get"]
        if names is not None and op.get("operationId") not in names:
            continue
        merged = {}
        for raw in [*item.get("parameters", []), *op.get("parameters", [])]:
            parameter = resolve(spec, raw)
            merged[(parameter["in"], parameter["name"])] = parameter
        scoped = any(p["name"] == "practiceId" for p in merged.values())
        if (not scoped and path != "/v1/practices") or "{patientId}" in path:
            continue
        name = op["operationId"]
        if name in operations or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name):
            raise ValueError(f"Duplicate or invalid operation ID: {name}")
        parameters = []
        for p in merged.values():
            if p["in"] not in {"path", "query"}:
                raise ValueError(f"Unsupported parameter location in {name}")
            if p.get("style", "simple" if p["in"] == "path" else "form") not in {"simple", "form"}:
                raise ValueError(f"Unsupported parameter serialization in {name}")
            normalized = schema(spec, p["schema"])
            if normalized.get("type") not in {"string", "integer", "number", "boolean"}:
                raise ValueError(f"Unsupported parameter type in {name}: {p['name']}")
            parameters.append(
                {
                    "name": p["name"],
                    "in": p["in"],
                    "required": bool(p.get("required", False)),
                    "schema": normalized,
                }
            )
        success = resolve(spec, op["responses"]["200"])
        operations[name] = {
            "method": "GET",
            "path": path,
            "description": " ".join(filter(None, [op.get("summary"), op.get("description")])),
            "parameters": sorted(parameters, key=lambda p: (p["in"], p["name"])),
            "response": schema(spec, success["content"]["application/json"]["schema"]),
        }
    if not operations:
        raise ValueError("No supported operations in specification")
    return {"source": SPEC_URL, "operations": operations}


def generate(snapshot: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    """Compile interface shapes and verified exceptions into runtime definitions."""
    operations = copy.deepcopy(snapshot["operations"])
    for patch in policy["patches"]:
        target = operations[patch["operation"]]
        for key in patch["path"][:-1]:
            target = target[key]
        leaf = patch["path"][-1]
        if target[leaf] != patch["expected"]:
            raise ValueError(f"Compatibility exception needs review: {patch['operation']}")
        target[leaf] = patch["value"]
    for name, op in operations.items():
        if op["method"] != "GET" or not op["path"].startswith("/v1/"):
            raise ValueError(f"Only business reads can be generated: {name}")
        if any(word in op["path"] for word in ("oauth", "webhook", "{patientId}", "..", "?")):
            raise ValueError(f"Excluded operation: {name}")
        op["name"] = name
        query = {p["name"] for p in op["parameters"] if p["in"] == "query"}
        op["paginated"] = {"limit", "offsetId"} <= query
        op["tool_schema"] = {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        }
        op["request_schema"] = {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        }
        scope = [p for p in op["parameters"] if p["name"] == "practiceId"]
        op["exposed"] = len(scope) == 1
        if not op["exposed"] and op["path"] != "/v1/practices":
            raise ValueError(f"Operation has no enforceable practice scope: {name}")
        for p in op["parameters"]:
            key, value = p["name"], p["schema"]
            if key in op["request_schema"]["properties"]:
                raise ValueError(f"Ambiguous parameter: {name}/{key}")
            op["request_schema"]["properties"][key] = value
            if p["required"]:
                op["request_schema"]["required"].append(key)
            if key == "practiceId":
                continue
            op["tool_schema"]["properties"][key] = (
                value if p["required"] else {"anyOf": [value, {"type": "null"}]}
            )
            op["tool_schema"]["required"].append(key)
        op["complete_listing"] = name in policy["complete_listings"]
        op["description"] = op["description"] or re.sub(r"(?<=[a-z])(?=[A-Z])", " ", name)
        op["description"] += ". Read only; restricted to this practice."
        if not op["complete_listing"]:
            op["description"] += " Results may be partial; never infer a population total."
        if name in policy["notes"]:
            op["description"] += " " + policy["notes"][name]
        for key in ("request_schema", "tool_schema", "response"):
            Draft202012Validator.check_schema(op[key])
    return {
        "source_hash": hashlib.sha256(rendered(snapshot).encode()).hexdigest(),
        "operations": operations,
    }


def difference(before: object, after: object) -> str:
    """A structural diff without live record values."""
    return "".join(
        difflib.unified_diff(
            rendered(before).splitlines(True),
            rendered(after).splitlines(True),
            fromfile="released interface",
            tofile="candidate interface",
        )
    )


def check(root: Path) -> bool:
    """Compare generated output without changing the snapshot or working tree."""
    expected = rendered(
        generate(
            json.loads((root / SNAPSHOT).read_text(encoding="utf-8")),
            json.loads((root / POLICY).read_text(encoding="utf-8")),
        )
    )
    return (root / ARTIFACT).read_text(encoding="utf-8") == expected


def check_staged(root: Path) -> int:
    """Run the staged generator against staged inputs, isolated from unstaged edits."""
    with tempfile.TemporaryDirectory(prefix="principle-generation-") as folder:
        destination = Path(folder)
        for path in (Path("scripts/refresh_spec.py"), SNAPSHOT, POLICY, ARTIFACT):
            raw = subprocess.run(
                ["git", "show", f":{path.as_posix()}"], cwd=root, check=True, capture_output=True
            ).stdout
            file = destination / path
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_bytes(raw)
        return subprocess.run(
            [sys.executable, "-I", str(destination / "scripts/refresh_spec.py"),
             "--check-generated"],
            cwd=destination,
            check=False,
        ).returncode


def main() -> int:
    """Regenerate, verify offline, or compare the live interface before release."""
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", "--check-generated", action="store_true")
    for flag in ("generate", "staged", "upstream-check", "update"):
        mode.add_argument(f"--{flag}", action="store_true")
    args = parser.parse_args()
    try:
        if args.staged:
            return check_staged(ROOT)
        if args.check:
            if check(ROOT):
                print("Generated Principle interface matches its snapshot.")
                return 0
            print(
                "Generated interface is stale or edited. Run:\n"
                "  uv run python -m scripts.refresh_spec --generate\n"
                "Then review and stage the generated artifact.",
                file=sys.stderr,
            )
            return 1
        snapshot = json.loads(FINGERPRINT.read_text(encoding="utf-8"))
        policy = json.loads((ROOT / POLICY).read_text(encoding="utf-8"))
        if args.update or args.upstream_check:
            candidate = build(fetch(), set(snapshot["operations"]) if args.upstream_check else None)
            relevant = {name: candidate["operations"].get(name) for name in snapshot["operations"]}
            delta = difference(
                snapshot["operations"], relevant if args.upstream_check else candidate["operations"]
            )
            print(delta or "Published interface matches released operations.")
            if args.upstream_check:
                if delta:
                    print("Run: uv run python -m scripts.refresh_spec --update")
                return int(bool(delta))
            snapshot = candidate
        artifact = rendered(generate(snapshot, policy))
        FINGERPRINT.write_text(rendered(snapshot), encoding="utf-8")
        (ROOT / ARTIFACT).write_text(artifact, encoding="utf-8")
        print("Generated interface. Review and commit the snapshot and artifact together.")
        if args.update:
            return subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pytest",
                    "-q",
                    "tests/test_generated_principle.py",
                    "tests/test_chat_turn.py",
                    "tests/test_client_against_fake.py",
                ],
                cwd=ROOT,
                check=False,
            ).returncode
        return 0
    except (ValueError, KeyError, OSError, subprocess.CalledProcessError) as error:
        print(f"Interface check failed ({type(error).__name__}): {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
