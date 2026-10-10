"""Run staging, or an explicitly selected diagnostic combination, in the foreground."""

from __future__ import annotations

import argparse
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx2 as httpx
from pydantic import SecretStr

from dental_practice_admin.config import (
    ENVIRONMENT_FIELDS,
    Environment,
    Settings,
    SignIn,
    environment_suffix,
)

ROOT = Path(__file__).resolve().parents[1]
FAKE_BANK_URL = "http://127.0.0.1:8897/v1"


def configuration(args: argparse.Namespace) -> Settings:
    """CLI selections override .env; absent selections preserve its independent settings."""
    overrides: dict[str, Any] = {}
    principle = args.principle or args.preset
    ai = args.ai or ("fake" if args.preset is Environment.FAKE else "real" if args.preset else None)
    if principle is not None:
        overrides["environment"] = principle
    if ai is not None:
        overrides["openai_base_url"] = (
            "http://127.0.0.1:8899/v1" if ai == "fake" else "https://api.openai.com/v1"
        )
        if ai == "fake":
            overrides["openai_api_key"] = "fake-openai-key"
    if args.sign_in is not None:
        overrides["sign_in"] = args.sign_in
    if args.preset is Environment.FAKE:
        # The preset is the fakes and a local address, whatever .env names, so the servers it
        # starts are the ones it uses. Imported here so that a real run never loads test code.
        from tests.fake_akahu import FAKE_AKAHU_SETTINGS
        overrides |= {"akahu_base_url": FAKE_BANK_URL, "public_base_url": "http://localhost:8080",
                      **FAKE_AKAHU_SETTINGS}
    settings = Settings(**overrides)
    settings.require_web_configured()
    if (
        urlsplit(settings.public_base_url).hostname not in {"localhost", "127.0.0.1"}
        and settings.chatkit_domain_key == "domain_pk_localhost"
    ):
        raise ValueError("ADMIN_CHATKIT_DOMAIN_KEY must be registered for the public hostname")
    return settings


def child_environment(settings: Settings) -> dict[str, str]:
    """Pass the resolved configuration to children without rereading or altering the parent."""
    env = dict(os.environ)
    for name, field in Settings.model_fields.items():
        key = field.validation_alias or f"ADMIN_{name.upper()}"
        assert isinstance(key, str)
        if name in ENVIRONMENT_FIELDS:
            suffix = environment_suffix(settings.environment)
            key = f"{key}_{suffix}"
        value = getattr(settings, name)
        env[key] = value.get_secret_value() if isinstance(value, SecretStr) else str(value)
    return env


def wait_for_server(url: str, child: subprocess.Popen[bytes], statuses: set[int]) -> None:
    """Refuse a failed or hung startup before starting dependent processes."""
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        if child.poll() is not None:
            raise RuntimeError(f"Server exited before answering {url}")
        try:
            if httpx.get(url, timeout=2).status_code in statuses:
                return
        except httpx.TransportError:
            pass
        time.sleep(0.2)
    raise TimeoutError(f"Server did not answer {url} within 45 seconds")


def run(settings: Settings) -> None:
    """Own the foreground processes and stop them if any required service exits."""
    children: list[subprocess.Popen[bytes]] = []
    env = child_environment(settings)
    origin = urlsplit(settings.public_base_url)
    tunnel = origin.hostname not in {"localhost", "127.0.0.1"}
    if tunnel and (origin.scheme != "https" or not shutil.which("ngrok")):
        raise ValueError("The public HTTPS address requires ngrok on PATH")
    services: list[tuple[str, int, str, set[int]]] = []
    if settings.environment is Environment.FAKE:
        # The fake Principle is served where PRINCIPLE_API_BASE_URL_FAKE says it is.
        address = urlsplit(settings.api_base_url)
        port = address.port
        if address.hostname not in {"127.0.0.1", "localhost"} or port is None:
            raise ValueError("PRINCIPLE_API_BASE_URL_FAKE must be a port on this machine")
        services.append(("tests.fake.server:app", port, "/v1/practices", {401, 403}))
    if settings.openai_base_url == "http://127.0.0.1:8899/v1":
        services.append(("tests.fake_ai.server:app", 8899, "/health", {200}))
    if settings.akahu_base_url == FAKE_BANK_URL:
        services.append(("tests.fake_akahu.server:app", 8897, "/v1/me", {401}))
    services.append(("dental_practice_admin.app:create_app", 8080, "/health", {200}))
    # An existing listener must not be mistaken for the child we are starting.
    for _, port, _, _ in services:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", port))
    try:
        for target, port, path, statuses in services:
            command = [
                sys.executable,
                "-m",
                "uvicorn",
                target,
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--proxy-headers",
                "--forwarded-allow-ips",
                "127.0.0.1",
            ]
            if target.endswith(":create_app"):
                command.append("--factory")
            child = subprocess.Popen(command, cwd=ROOT, env=env)
            children.append(child)
            wait_for_server(f"http://127.0.0.1:{port}{path}", child, statuses)
        if tunnel:
            children.append(
                subprocess.Popen(
                    ["ngrok", "http", f"--url={settings.public_base_url}", "8080", "--log=stdout"],
                    cwd=ROOT,
                )
            )
        print(f"Open {settings.public_base_url} ({settings.environment.value})", flush=True)
        while all(child.poll() is None for child in children):
            time.sleep(0.5)
        raise RuntimeError("A required service exited; stopping this run")
    finally:
        for child in reversed(children):
            if child.poll() is None:
                child.terminate()
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()


def main() -> int:
    """No arguments starts staging; exceptions remain command-line diagnostics."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preset", type=Environment, choices=list(Environment))
    parser.add_argument("--principle", type=Environment, choices=list(Environment))
    parser.add_argument(
        "--sign-in",
        type=SignIn,
        choices=list(SignIn),
        help="developer DISABLES GOOGLE LOGIN and allows anyone access",
    )
    parser.add_argument("--ai", choices=["real", "fake"])
    args = parser.parse_args()
    try:
        settings = configuration(args)
        if settings.environment is Environment.PRODUCTION:
            print("WARNING: THIS RUN ACCESSES LIVE PRINCIPLE DATA", flush=True)
        run(settings)
    except KeyboardInterrupt:
        return 0
    except Exception as error:
        print(f"Cannot run: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
