"""Update or validate the released example-free OpenAPI document."""
from __future__ import annotations

import argparse
import difflib
import json
import subprocess
import urllib.request
from pathlib import Path
from typing import Any

import yaml
from openapi_spec_validator import validate

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = Path("src/dental_practice_admin/principle_openapi.json")
SPEC_URL = "https://api.principle.dental/assets/api.yml"


def fetch() -> str:
    """Read the public interface without credentials."""
    with urllib.request.urlopen(SPEC_URL, timeout=60) as response:
        return str(response.read().decode("utf-8"))


def build(text: str) -> dict[str, Any]:
    """Keep the interface shape, without examples or vendor prose."""
    def clean(value: Any) -> Any:
        if isinstance(value, list):
            return [clean(item) for item in value]
        if isinstance(value, dict):
            return {k: "" if k == "description" and isinstance(v, str) else clean(v)
                    for k, v in value.items()
                    if k not in {"example", "examples", "externalDocs", "summary"}}
        return value
    spec: dict[str, Any] = clean(yaml.safe_load(text))
    spec["info"] = {"title": "Principle interface", "version": "released"}
    spec.pop("servers", None)
    # OpenAPI requires response descriptions even when no prose is distributed.
    for path in spec["paths"].values():
        for operation in path.values():
            if isinstance(operation, dict) and "responses" in operation:
                for response in operation["responses"].values():
                    if "$ref" not in response:
                        response["description"] = "Response"
    for response in spec.get("components", {}).get("responses", {}).values():
        response["description"] = "Response"
    validate(spec)
    return spec


def main() -> int:
    """Updates are explicit; startup and offline checks never fetch upstream."""
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ("update", "check", "staged", "upstream-check"):
        parser.add_argument("--" + flag, action="store_true")
    args = parser.parse_args()
    if args.staged:
        result = subprocess.run(["git", "show", ":" + SNAPSHOT.as_posix()],
                                capture_output=True, check=True, cwd=ROOT)
        validate(json.loads(result.stdout))
        return 0
    current = json.loads((ROOT / SNAPSHOT).read_text(encoding="utf-8"))
    validate(current)
    if not (args.update or args.upstream_check):
        print("Released OpenAPI document is valid.")
        return 0
    published = build(fetch())
    before = json.dumps(current, indent=2, sort_keys=True)
    after = json.dumps(published, indent=2, sort_keys=True)
    print("\n".join(difflib.unified_diff(before.splitlines(), after.splitlines())))
    if args.update:
        (ROOT / SNAPSHOT).write_text(after + "\n", encoding="utf-8")
        return 0
    return int(current != published)


if __name__ == "__main__":
    raise SystemExit(main())
