"""The recorder reads production and writes only to staging (ADR 0006)."""
import json
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import httpx2 as httpx
import pytest
import scripts.record_principle_wire as recorder

from dental_practice_admin.config import (
    PRODUCTION_API_URL,
    STAGING_API_URL,
    ConfigurationError,
    Environment,
)
from dental_practice_admin.firestore import Firestore
from dental_practice_admin.principle import PrincipleClient
from tests.fake import seed, transport
from tests.settings import fake_settings

Sent = list[tuple[str, str, str]]


class SeededDay(date):
    """The day after the fake's first diary day, so its appointments are recent."""

    @classmethod
    def today(cls) -> SeededDay:
        return cls(2026, 9, 29)


def _environment(tmp_path: Path, environment: Environment, url: str) -> dict[str, str]:
    return fake_settings(tmp_path, environment=environment, api_base_url=url).as_environment()


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Sent:
    """Run the recorder against one fake Principle, noting each request's environment.

    Production and staging differ by name only; the fake answers both, so a request reaching
    the wrong one is visible here rather than in a wrong recording.
    """
    for name, value in (_environment(tmp_path, Environment.PRODUCTION, PRODUCTION_API_URL)
                        | _environment(tmp_path, Environment.STAGING, STAGING_API_URL)).items():
        monkeypatch.setenv(name, value)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(recorder, "RECORDINGS", tmp_path / "recordings")
    monkeypatch.setattr(recorder, "FIRESTORE", tmp_path / "recordings" / "firestore")
    monkeypatch.setattr(recorder, "date", SeededDay)
    fake = transport(seed())
    seen: Sent = []

    def noting(environment: str) -> httpx.AsyncBaseTransport:
        class Noting(httpx.AsyncBaseTransport):
            async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
                seen.append((environment, request.method, request.url.path))
                return await fake.handle_async_request(request)
        return Noting()

    def by_host(url: str) -> str:
        return "production" if url.startswith(PRODUCTION_API_URL) else "staging"

    monkeypatch.setattr(recorder, "PrincipleClient", lambda settings, **kw: PrincipleClient(
        settings, transport=noting(settings.environment.value), **kw))
    monkeypatch.setattr(recorder, "Firestore", lambda settings: Firestore(
        settings, transport=noting(settings.environment.value)))
    monkeypatch.setattr(recorder, "httpx", SimpleNamespace(
        AsyncClient=lambda base_url, **kw: httpx.AsyncClient(
            base_url=base_url, transport=noting(by_host(base_url)), **kw)))
    return seen


async def _record(monkeypatch: pytest.MonkeyPatch, *flags: str) -> None:
    monkeypatch.setattr(sys, "argv", ["record_principle_wire.py", *flags])
    await recorder.main()


def _sources(tmp_path: Path) -> dict[str, str]:
    return {path.stem: json.loads(path.read_text(encoding="utf-8"))["source"]
            for path in (tmp_path / "recordings").rglob("*.json")}


async def test_a_plain_run_reads_only_production(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path, sent: Sent) -> None:
    await _record(monkeypatch)

    assert sent
    assert {environment for environment, _, _ in sent} == {"production"}
    assert all("production" in source or source == "api.principle.dental"
               for source in _sources(tmp_path).values())


async def test_creating_a_patient_writes_only_to_staging(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path, sent: Sent) -> None:
    await _record(monkeypatch, "--create-patient")

    writes = [(environment, path) for environment, method, path in sent
              if method == "POST" and path == "/v1/patients"]
    assert writes == [("staging", "/v1/patients")]
    assert {environment for environment, _, path in sent
            if path != "/v1/patients"} == {"production"}
    assert _sources(tmp_path)["create_patient"] == "api.staging.principle.dental"


async def test_a_write_never_reaches_production(
        monkeypatch: pytest.MonkeyPatch, sent: Sent) -> None:
    monkeypatch.setenv("PRINCIPLE_API_BASE_URL_STAGING", PRODUCTION_API_URL)

    with pytest.raises(ConfigurationError):
        await _record(monkeypatch, "--create-patient")
    assert sent == []


async def test_a_plain_run_needs_no_staging_settings(
        monkeypatch: pytest.MonkeyPatch, sent: Sent) -> None:
    monkeypatch.delenv("PRINCIPLE_API_KEY_STAGING")

    await _record(monkeypatch)

    assert sent


async def test_creating_without_staging_settings_refuses_before_reading(
        monkeypatch: pytest.MonkeyPatch, sent: Sent) -> None:
    monkeypatch.delenv("PRINCIPLE_API_KEY_STAGING")

    with pytest.raises(ConfigurationError):
        await _record(monkeypatch, "--create-patient")
    assert sent == []
