"""The recorder reads production and writes only to staging (ADR 0006)."""
import json
import sys
from collections.abc import AsyncIterator
from datetime import date, datetime, timedelta
from pathlib import Path

import httpx2 as httpx
import pytest
import scripts.record_principle_wire as recorder

from dental_practice_admin.config import (
    PRODUCTION_API_URL,
    STAGING_API_URL,
    ConfigurationError,
    Environment,
    Settings,
)
from tests.fake import FakeStore, transport
from tests.settings import fake_settings


class Sent(list[tuple[str, httpx.Request]]):
    """Each request with its environment, and every response body exactly as the fake sent it."""

    bodies: list[bytes]

TODAY = date(2026, 9, 29)
OWNER_MOBILE, OWNER_EMAIL = "+6421000000", "owner@example.invalid"


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
    monkeypatch.setenv("ADMIN_OWNER_MOBILE", OWNER_MOBILE)
    monkeypatch.setenv("ADMIN_OWNER_EMAIL", OWNER_EMAIL)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(recorder, "REFUSALS", tmp_path / "refusals")
    monkeypatch.setattr(recorder, "date", SeededDay)
    fake = transport(fake_store)
    seen = Sent()
    seen.bodies = []

    def noting(settings: Settings) -> httpx.AsyncBaseTransport:
        class Teed(httpx.AsyncByteStream):
            """The fake's body as it streams, copied as it goes, as a network response is."""

            def __init__(self, inner: httpx.AsyncByteStream) -> None:
                self.inner = inner

            async def __aiter__(self) -> AsyncIterator[bytes]:
                chunks = []
                async for chunk in self.inner:
                    chunks.append(chunk)
                    yield chunk
                seen.bodies.append(b"".join(chunks))

            async def aclose(self) -> None:
                await self.inner.aclose()

        class Noting(httpx.AsyncBaseTransport):
            async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
                seen.append((settings.environment.value, request))
                response = await fake.handle_async_request(request)
                assert isinstance(response.stream, httpx.AsyncByteStream)
                response.stream = Teed(response.stream)
                return response
        return Noting()

    monkeypatch.setattr(recorder, "transport_for", noting)
    return seen


async def _record(monkeypatch: pytest.MonkeyPatch, *flags: str) -> None:
    monkeypatch.setattr(sys, "argv", ["record_principle_wire.py", *flags])
    await recorder.main()


def _recordings(tmp_path: Path) -> Path:
    """Where production's recordings belong: its data folder, never this checkout."""
    return tmp_path / Environment.PRODUCTION.value / "recordings"


def _hosts(folder: Path) -> dict[str, set[str]]:
    """Each recorded operation, with the hosts that answered it."""
    hosts: dict[str, set[str]] = {}
    for path in folder.rglob("*.json"):
        url = json.loads(path.read_text(encoding="utf-8"))["url"]
        hosts.setdefault(path.parent.relative_to(folder).as_posix(), set()).add(
            httpx.URL(url).host)
    return hosts


def _refusals(tmp_path: Path) -> set[str]:
    return {path.stem for path in (tmp_path / "refusals").rglob("*.json")}


def _creates(sent: Sent) -> list[tuple[str, httpx.Request]]:
    return [(environment, request) for environment, request in sent
            if request.method == "POST" and request.url.path == "/v1/patients"]


async def test_a_plain_run_reads_only_production(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path, sent: Sent) -> None:
    await _record(monkeypatch)

    assert sent
    assert {environment for environment, _ in sent} == {"production"}
    assert not any(STAGING_API_URL.removeprefix("https://") in hosts
                   for hosts in _hosts(_recordings(tmp_path)).values())


async def test_a_recording_is_exactly_what_the_system_sent(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path, sent: Sent) -> None:
    await _record(monkeypatch)

    recorded = [path.read_bytes() for path in _recordings(tmp_path).rglob("*.body")]
    assert sorted(recorded) == sorted(sent.bodies)


async def test_recordings_stay_in_the_data_folder_and_refusals_in_the_repository(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path, sent: Sent) -> None:
    await _record(monkeypatch)

    assert {"getPatient", "listAppointmentsByDateRange"} <= _hosts(_recordings(tmp_path)).keys()
    assert "unauthorised" in _refusals(tmp_path)
    assert not {"getPatient", "patient"} & _refusals(tmp_path)


async def test_creating_a_patient_writes_only_to_staging_and_last(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path, sent: Sent) -> None:
    await _record(monkeypatch, "--create-patient")

    environments = [environment for environment, _ in sent]
    first = environments.index("staging")
    assert [environment for environment, _ in _creates(sent)] == ["staging"]
    assert set(environments[:first]) == {"production"}
    assert set(environments[first:]) == {"staging"}


async def test_writes_are_recorded_in_stagings_own_data_folder(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path, sent: Sent) -> None:
    await _record(monkeypatch, "--create-patient")

    staging = _hosts(tmp_path / Environment.STAGING.value / "recordings")
    assert {"createPatient", "updatePatient"} <= staging.keys()
    assert set().union(*staging.values()) == {STAGING_API_URL.removeprefix("https://")}
    assert not {"createPatient", "updatePatient"} & _hosts(_recordings(tmp_path)).keys()


def _updates(sent: Sent) -> list[httpx.Request]:
    return [request for _, request in sent if request.method == "PATCH"]


async def test_the_update_goes_only_to_the_patient_just_created(
        monkeypatch: pytest.MonkeyPatch, fake_store: FakeStore, sent: Sent) -> None:
    before = {row["id"] for row in fake_store.db.execute("SELECT id FROM patients")}

    await _record(monkeypatch, "--create-patient")

    created = {row["id"] for row in fake_store.db.execute("SELECT id FROM patients")} - before
    assert [request.url.path for request in _updates(sent)] == [
        f"/v1/patients/{patient}" for patient in created]


async def test_the_update_is_refused_unless_the_patient_carries_the_owners_details(
        monkeypatch: pytest.MonkeyPatch, fake_store: FakeStore, sent: Sent) -> None:
    create = fake_store.create_patient
    monkeypatch.setattr(fake_store, "create_patient", lambda body, at: create(
        body | {"email": "someone.else@example.invalid"}, at))

    with pytest.raises(SystemExit):
        await _record(monkeypatch, "--create-patient")
    assert _updates(sent) == []


async def test_the_created_patient_carries_the_fake_marker(
        monkeypatch: pytest.MonkeyPatch, sent: Sent) -> None:
    await _record(monkeypatch, "--create-patient")

    (_, request), = _creates(sent)
    assert "Fake" in json.loads(request.content)["name"]


async def test_the_created_patient_carries_the_owners_phone_and_email(
        monkeypatch: pytest.MonkeyPatch, sent: Sent) -> None:
    await _record(monkeypatch, "--create-patient")

    (_, request), = _creates(sent)
    body = json.loads(request.content)
    assert body["contactNumbers"] == [{"label": "mobile", "number": OWNER_MOBILE}]
    assert body["email"] == OWNER_EMAIL


@pytest.mark.parametrize("missing", ["ADMIN_OWNER_MOBILE", "ADMIN_OWNER_EMAIL"])
async def test_creating_without_the_owners_contact_details_refuses_before_reading(
        monkeypatch: pytest.MonkeyPatch, sent: Sent, missing: str) -> None:
    monkeypatch.setenv(missing, "")

    with pytest.raises(ConfigurationError):
        await _record(monkeypatch, "--create-patient")
    assert sent == []


async def test_a_failed_run_creates_nothing_and_keeps_the_previous_recordings(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake_store: FakeStore,
        sent: Sent) -> None:
    previous = _recordings(tmp_path) / "listPractices" / "001.body"
    previous.parent.mkdir(parents=True)
    previous.write_text("previous", encoding="utf-8")
    fake_store.db.execute("DELETE FROM calendar_events")
    fake_store.db.commit()

    with pytest.raises(SystemExit):
        await _record(monkeypatch, "--create-patient")

    assert _creates(sent) == []
    assert [path for path in _recordings(tmp_path).rglob("*") if path.is_file()] == [previous]
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

    assert {"getPatient", "searchPatients"} <= _hosts(_recordings(tmp_path)).keys()


def _recorded_days_ago(folder: Path, days: int) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "listPractices.json").write_text(json.dumps(
        {"capturedOn": (TODAY - timedelta(days=days)).isoformat()}), encoding="utf-8")


async def test_a_recording_older_than_sixty_days_fails_the_age_check(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path, sent: Sent) -> None:
    _recorded_days_ago(_recordings(tmp_path), 61)

    with pytest.raises(SystemExit) as refused:
        await _record(monkeypatch, "--older-than")
    assert refused.value.code == 1
    assert sent == []


async def test_recordings_within_sixty_days_pass_the_age_check(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path, sent: Sent) -> None:
    _recorded_days_ago(_recordings(tmp_path), 59)

    await _record(monkeypatch, "--older-than")
    assert sent == []


async def test_the_age_check_takes_its_limit_in_days(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path, sent: Sent) -> None:
    _recorded_days_ago(_recordings(tmp_path), 10)

    with pytest.raises(SystemExit) as refused:
        await _record(monkeypatch, "--older-than", "7")
    assert refused.value.code == 1
    assert sent == []


async def test_the_age_check_fails_when_there_are_no_recordings(
        monkeypatch: pytest.MonkeyPatch, sent: Sent) -> None:
    """A wrong data folder holds no recordings, which would otherwise pass as all being fresh."""
    with pytest.raises(SystemExit) as refused:
        await _record(monkeypatch, "--older-than")
    assert refused.value.code == 1


async def test_a_recording_carries_the_day_it_was_taken_on_this_machine(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path, sent: Sent) -> None:
    """The day the age check measures from; a UTC day is yesterday for a New Zealand morning."""
    await _record(monkeypatch)

    days = {json.loads(path.read_text(encoding="utf-8"))["capturedOn"]
            for folder in (_recordings(tmp_path), tmp_path / "refusals")
            for path in folder.rglob("*.json")}
    assert days == {TODAY.isoformat()}


async def test_a_run_keeps_the_recordings_of_operations_it_did_not_make(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path, sent: Sent) -> None:
    kept = _recordings(tmp_path) / "createPatient" / "001.body"
    kept.parent.mkdir(parents=True)
    kept.write_bytes(b"from an earlier run")

    await _record(monkeypatch)

    assert kept.read_bytes() == b"from an earlier run"


async def test_a_run_replaces_the_recordings_of_operations_it_made(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path, sent: Sent) -> None:
    stale = _recordings(tmp_path) / "listPractices" / "009.body"
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b"from an earlier run")

    await _record(monkeypatch)

    assert not stale.exists()


@pytest.mark.parametrize("table", ["calendar_events", "roster_items", "invoices", "transactions"])
async def test_a_collection_with_nothing_to_record_fails_the_run(
        monkeypatch: pytest.MonkeyPatch, fake_store: FakeStore, sent: Sent, table: str) -> None:
    fake_store.db.execute(f"DELETE FROM {table}")
    fake_store.db.commit()

    with pytest.raises(SystemExit):
        await _record(monkeypatch)


async def test_a_run_records_what_bank_reconciliation_reads(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path, sent: Sent) -> None:
    await _record(monkeypatch)

    assert {"listInvoicesByDateRange", "listTransactionsByDateRange"} <= _hosts(
        _recordings(tmp_path)).keys()
