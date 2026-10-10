"""One record that breaks the specification is named, not allowed to fail everything else."""
import json
from pathlib import Path
from typing import Any

import httpx2 as httpx
import pytest
from pydantic import SecretStr

from dental_practice_admin.config import PRODUCTION_API_URL, Environment, Settings
from dental_practice_admin.principle import (
    CallError,
    PrincipleClient,
    PrincipleError,
    RecordError,
)
from dental_practice_admin.storage import Storage
from tests.settings import fake_settings

GOOD = {"id": "fake-good", "name": "Fake Dummy", "email": "good@example.invalid",
        "gender": "notSpecified", "address": "Fake address", "dateOfBirth": "2000-01-01",
        "contactNumbers": [{"label": "mobile", "number": "+64210000000"}]}
BAD = {**GOOD, "id": "fake-bad", "email": "bad@example.invalid",
       "contactNumbers": [{"label": "mobile", "number": "ring mum"}]}


def _production(tmp_path: Path) -> Settings:
    return fake_settings(tmp_path, environment=Environment.PRODUCTION,
                         api_base_url=PRODUCTION_API_URL, api_key=SecretStr("fake-key"),
                         practice_id="fake-practice")


def _principle(patients: dict[str, dict[str, Any]], practice: list[str],
               sent: list[httpx.Request]) -> httpx.MockTransport:
    """Principle holding `patients`, of which the ids in `practice` belong to this practice."""
    def respond(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        if request.url.path == "/v1/patients":
            return httpx.Response(200, json={"data": [patients[i] for i in practice]})
        patient_id = request.url.path.rsplit("/", 1)[1]
        if request.method == "PATCH":
            change = json.loads(request.content)
            change.pop("practiceId")
            patients[patient_id] = {**patients[patient_id], **change}
        return httpx.Response(200, json=patients[patient_id])
    return httpx.MockTransport(respond)


def _warnings(settings: Settings) -> list[dict[str, Any]]:
    store = Storage(settings.database_path)
    try:
        return store.interface_warnings()
    finally:
        store.close()


async def test_bad_contact_details_name_the_patient_without_claiming_an_interface_change(
    tmp_path: Path,
) -> None:
    settings = _production(tmp_path)
    cases: list[dict[str, Any]] = [BAD, {**GOOD, "email": "No email"}]
    for broken in cases:
        transport = _principle({broken["id"]: broken}, [broken["id"]], [])
        async with PrincipleClient(settings, transport=transport) as client:
            with pytest.raises(RecordError) as caught:
                await client.call("getPatient", {"patientId": broken["id"]})
        assert caught.value.records == [broken["id"]]
        assert caught.value.body == broken
    assert not _warnings(settings)


@pytest.mark.parametrize("change", [{"gender": "nonsense"}, {"contactNumbers": "not a list"},
                                    {"contactNumbers": [{"number": "+64210000000"}]},
                                    {"email": {"address": "fake@example.invalid"}},
                                    {"contactNumbers": [{"label": "mobile", "number": 64}]}])
async def test_any_other_break_in_a_patient_is_an_interface_change(
    tmp_path: Path, change: dict[str, Any],
) -> None:
    settings = _production(tmp_path)
    transport = _principle({"fake-bad": {**BAD, **change}}, ["fake-bad"], [])
    async with PrincipleClient(settings, transport=transport) as client:
        with pytest.raises(PrincipleError) as caught:
            await client.call("getPatient", {"patientId": "fake-bad"})
    assert not isinstance(caught.value, RecordError)
    assert [w["operation"] for w in _warnings(settings)] == ["getPatient"]


async def test_a_namesake_breaking_the_specification_does_not_fail_a_good_patient(
    tmp_path: Path,
) -> None:
    transport = _principle({"fake-good": GOOD, "fake-bad": BAD}, ["fake-bad", "fake-good"], [])
    async with PrincipleClient(_production(tmp_path), transport=transport) as client:
        assert await client.call("getPatient", {"patientId": "fake-good"}) == GOOD


async def test_a_search_names_only_the_breaking_rows(tmp_path: Path) -> None:
    transport = _principle({"fake-good": GOOD, "fake-bad": BAD}, ["fake-bad", "fake-good"], [])
    async with PrincipleClient(_production(tmp_path), transport=transport) as client:
        with pytest.raises(RecordError) as caught:
            await client.call("searchPatients", {"name": "Fake Dummy"})
    assert caught.value.records == ["fake-bad"]


async def test_a_breaking_patient_outside_the_practice_is_still_refused(
    tmp_path: Path,
) -> None:
    sent: list[httpx.Request] = []
    transport = _principle({"fake-good": GOOD, "fake-bad": BAD}, ["fake-good"], sent)
    async with PrincipleClient(_production(tmp_path), transport=transport) as client:
        with pytest.raises(CallError):
            await client.call("getPatient", {"patientId": "fake-bad"})
        with pytest.raises(CallError):
            await client.call("updatePatient", {
                **{k: v for k, v in BAD.items() if k != "id"},
                "patientId": "fake-bad", "contactNumbers": GOOD["contactNumbers"]})
    assert all(request.method == "GET" for request in sent)


async def test_a_patient_breaking_the_specification_can_be_fixed(tmp_path: Path) -> None:
    sent: list[httpx.Request] = []
    transport = _principle({"fake-bad": dict(BAD)}, ["fake-bad"], sent)
    async with PrincipleClient(_production(tmp_path), transport=transport) as client:
        fixed = await client.call("updatePatient", {
            **{k: v for k, v in BAD.items() if k != "id"},
            "patientId": "fake-bad", "contactNumbers": GOOD["contactNumbers"]})
    assert fixed["contactNumbers"] == GOOD["contactNumbers"]
    assert [r.method for r in sent].count("PATCH") == 1


@pytest.mark.parametrize("body", [
    {"data": [{"name": "no identifier", "contactNumbers": "not a list"}]},
    {"data": "not a list"},
    {"id": 42, "name": "identifier of the wrong type"},
])
async def test_a_response_with_no_identifiable_record_is_still_an_incompatibility(
    tmp_path: Path, body: dict[str, Any],
) -> None:
    transport = httpx.MockTransport(lambda _: httpx.Response(200, json=body))
    async with PrincipleClient(_production(tmp_path), transport=transport) as client:
        with pytest.raises(PrincipleError) as caught:
            await client.call("searchPatients", {"name": "Fake Dummy"})
    assert not isinstance(caught.value, RecordError)


async def test_a_broken_page_envelope_is_not_blamed_on_its_records(tmp_path: Path) -> None:
    body = {"data": [{"id": "fake-tag"}], "meta": {"limit": "not a number"}}
    transport = httpx.MockTransport(lambda _: httpx.Response(200, json=body))
    async with PrincipleClient(_production(tmp_path), transport=transport) as client:
        with pytest.raises(PrincipleError) as caught:
            await client.call("listPatientTags", {})
    assert not isinstance(caught.value, RecordError)


async def test_a_write_that_saves_a_patient_with_bad_contact_details_succeeds(
    tmp_path: Path,
) -> None:
    transport = httpx.MockTransport(lambda _: httpx.Response(201, json=BAD))
    async with PrincipleClient(_production(tmp_path), transport=transport) as client:
        created = await client.call("createPatient", {
            k: v for k, v in GOOD.items() if k not in ("id", "address")})
    assert created == BAD


async def test_a_bad_record_does_not_hide_another_break_in_the_response(tmp_path: Path) -> None:
    transport = httpx.MockTransport(lambda _: httpx.Response(
        200, content=json.dumps(BAD), headers={"content-type": "text/plain"}))
    async with PrincipleClient(_production(tmp_path), transport=transport) as client:
        with pytest.raises(PrincipleError) as caught:
            await client.call("getPatient", {"patientId": "fake-bad"})
    assert not isinstance(caught.value, RecordError)


async def test_only_patient_responses_can_be_blamed_on_a_record(tmp_path: Path) -> None:
    body = {**GOOD, "email": "No email"}
    transport = httpx.MockTransport(lambda _: httpx.Response(200, json=body))
    async with PrincipleClient(_production(tmp_path), transport=transport) as client:
        with pytest.raises(PrincipleError) as caught:
            await client.call("getPractitioner", {"practitionerId": "fake-practitioner"})
    assert not isinstance(caught.value, RecordError)
