"""The recorder reads production and writes only to staging (ADR 0006)."""
import json
import sys
from datetime import date, datetime, timedelta
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
from tests.fake import FakeStore, transport
from tests.settings import fake_settings

Sent = list[tuple[str, httpx.Request]]
TODAY = date(2026, 9, 29)


class SeededDay(date):
    """The day after the fake's first diary day, so its appointments are recent."""

    @classmethod
    def today(cls) -> SeededDay:
        return cls(TODAY.year, TODAY.month, TODAY.day)


def _environment(tmp_path: Path, environment: Environment, url: str) -> dict[str, str]:
    return fake_settings(tmp_path, environment=environment, api_base_url=url).as_environment()


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake_store: FakeStore) -> Sent:
    """Run the recorder against one fake Principle, noting each request's environment.

    Production and staging differ by name only; the fake answers both, so a request reaching
    the wrong one is visible here rather than in a wrong recording.
    """
    for name, value in (_environment(tmp_path, Environment.PRODUCTION, PRODUCTION_API_URL)
                        | _environment(tmp_path, Environment.STAGING, STAGING_API_URL)).items():
        monkeypatch.setenv(name, value)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(recorder, "REFUSALS", tmp_path / "refusals")
    monkeypatch.setattr(recorder, "date", SeededDay)
    fake = transport(fake_store)
    seen: Sent = []

    def noting(environment: str) -> httpx.AsyncBaseTransport:
        class Noting(httpx.AsyncBaseTransport):
            async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
                seen.append((environment, request))
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


def _recordings(tmp_path: Path) -> Path:
    """Where production's recordings belong: its data folder, never this checkout."""
    return tmp_path / Environment.PRODUCTION.value / "recordings"


def _sources(folder: Path) -> dict[str, str]:
    return {path.stem: json.loads(path.read_text(encoding="utf-8"))["source"]
            for path in folder.rglob("*.json")}


def _creates(sent: Sent) -> list[tuple[str, httpx.Request]]:
    return [(environment, request) for environment, request in sent
            if request.method == "POST" and request.url.path == "/v1/patients"]


async def test_a_plain_run_reads_only_production(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path, sent: Sent) -> None:
    await _record(monkeypatch)

    assert sent
    assert {environment for environment, _ in sent} == {"production"}
    assert set(_sources(_recordings(tmp_path)).values()) <= {
        "api.principle.dental", "firestore (production)"}


async def test_recordings_stay_in_the_data_folder_and_refusals_in_the_repository(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path, sent: Sent) -> None:
    await _record(monkeypatch)

    assert {"patient", "appointments"} <= _sources(_recordings(tmp_path)).keys()
    assert "patient" not in _sources(tmp_path / "refusals")
    assert "unauthorised" in _sources(tmp_path / "refusals")


async def test_creating_a_patient_writes_only_to_staging_and_last(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path, sent: Sent) -> None:
    await _record(monkeypatch, "--create-patient")

    assert [environment for environment, _ in _creates(sent)] == ["staging"]
    assert sent[-1] == _creates(sent)[0]
    assert {environment for environment, _ in sent[:-1]} == {"production"}
    assert _sources(_recordings(tmp_path))["create_patient"] == "api.staging.principle.dental"


async def test_the_created_patient_carries_the_fake_marker(
        monkeypatch: pytest.MonkeyPatch, sent: Sent) -> None:
    await _record(monkeypatch, "--create-patient")

    (_, request), = _creates(sent)
    assert "Fake" in json.loads(request.content)["name"]


async def test_a_failed_run_creates_nothing_and_keeps_the_previous_recordings(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake_store: FakeStore,
        sent: Sent) -> None:
    previous = _recordings(tmp_path) / "practices.json"
    previous.parent.mkdir(parents=True)
    previous.write_text("previous", encoding="utf-8")
    fake_store.db.execute("DELETE FROM calendar_events")
    fake_store.db.commit()

    with pytest.raises(SystemExit):
        await _record(monkeypatch, "--create-patient")

    assert _creates(sent) == []
    assert [path.name for path in _recordings(tmp_path).rglob("*")] == ["practices.json"]
    assert previous.read_text(encoding="utf-8") == "previous"


async def test_a_mistyped_flag_is_refused_before_anything_is_sent(
        monkeypatch: pytest.MonkeyPatch, sent: Sent) -> None:
    with pytest.raises(SystemExit):
        await _record(monkeypatch, "--create-patients")
    assert sent == []


async def test_the_diary_is_read_a_week_either_side_of_today(
        monkeypatch: pytest.MonkeyPatch, sent: Sent) -> None:
    await _record(monkeypatch)

    listings = [request.url.params for _, request in sent
                if request.url.path.endswith("/appointments")]
    assert listings
    for params in listings:
        assert datetime.fromisoformat(params["from"]).date() >= TODAY - timedelta(days=7)
        assert datetime.fromisoformat(params["to"]).date() <= TODAY + timedelta(days=7)


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


async def test_a_patient_breaking_the_specification_is_recorded_as_sent(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake_store: FakeStore,
        sent: Sent) -> None:
    fake_store.db.execute("UPDATE patients SET phone = '021 123 4567'")
    fake_store.db.commit()

    await _record(monkeypatch)

    assert {"patient", "search_patients"} <= _sources(_recordings(tmp_path)).keys()
