"""Every setting for the fake environment, written out in full.

The application has no defaults, so a test states every setting it runs with. A test changes
only what it is about, with overrides; everything else is as here.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from dental_practice_admin.config import (
    FAKE_API_KEY,
    FAKE_API_URL,
    FAKE_FIREBASE_PROJECT,
    FAKE_FIRESTORE_ROOT,
    FAKE_PRACTICE_ID,
    PRODUCTION_API_URL,
    STAGING_API_URL,
    Environment,
    Settings,
    SignIn,
)
from tests.fake_ai import FAKE_AI_KEY
from tests.fake_akahu import FAKE_AKAHU_SETTINGS

API_URLS = {
    Environment.FAKE: FAKE_API_URL,
    Environment.STAGING: STAGING_API_URL,
    Environment.PRODUCTION: PRODUCTION_API_URL,
}

FAKE_SETTINGS: dict[str, Any] = {
    "environment": Environment.FAKE,
    "api_base_url": FAKE_API_URL,
    "api_key": SecretStr(FAKE_API_KEY),
    "practice_id": FAKE_PRACTICE_ID,
    "firebase_project": FAKE_FIREBASE_PROJECT,
    "firestore_root": FAKE_FIRESTORE_ROOT,
    "playwright_mcp_path": Path("node_modules/@playwright/mcp/cli.js"),
    "task_repository": "fake-owner/fake-tasks",
    "github_token": SecretStr("fake-github-token"),
    "sign_in": SignIn.DEVELOPER,
    "openai_api_key": SecretStr(FAKE_AI_KEY),
    "openai_base_url": "https://fake-ai.invalid/v1",
    "agent_model": "fake-model",
    "akahu_base_url": "https://fake-akahu.invalid/v1",
    "chatkit_domain_key": "domain_pk_localhost",
    "public_base_url": "",
    **FAKE_AKAHU_SETTINGS,
}


def fake_settings(data_root: Path, **overrides: Any) -> Settings:
    """The fake environment's settings, with runtime data under `data_root`, which is created."""
    data_root.mkdir(parents=True, exist_ok=True)
    return Settings(**(FAKE_SETTINGS | {"data_root": data_root} | overrides))


APPLICATION_PREFIXES = ("ADMIN_", "PRINCIPLE_", "OPENAI_", "AKAHU_")


def use_fake_environment(monkeypatch: pytest.MonkeyPatch, data_root: Path,
                         **overrides: Any) -> None:
    """Replace this machine's settings variables with the fake environment's."""
    for name in list(os.environ):
        if name.startswith(APPLICATION_PREFIXES):
            monkeypatch.delenv(name)
    for name, value in fake_environment(data_root, **overrides).items():
        if name.startswith(APPLICATION_PREFIXES):
            monkeypatch.setenv(name, value)


def fake_environment(data_root: Path, **overrides: Any) -> dict[str, str]:
    """The same settings as environment variables, named as `.env` names them."""
    from scripts.run import child_environment

    return child_environment(fake_settings(data_root, **overrides))
