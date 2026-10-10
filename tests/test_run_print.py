"""A run's printable page: saved beside its audit, served without scripts, to its owners only."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from dental_practice_admin import scripts
from dental_practice_admin.app import create_app
from dental_practice_admin.auth import FAKE_STAFF
from dental_practice_admin.config import Environment, Settings, SignIn
from dental_practice_admin.storage import Coverage, Outcome, Storage
from tests.fake_akahu import FAKE_AKAHU_SETTINGS
from tests.test_automation import draft

PAGE = "<!doctype html><html><body><section>Synthetic day sheet</section></body></html>"


@pytest.fixture
def configured(tmp_path: Path) -> Settings:
    return Settings(environment=Environment.FAKE, data_root=tmp_path,
                    sign_in=SignIn.DEVELOPER, openai_api_key=SecretStr("fake-ai-key"),
                    **FAKE_AKAHU_SETTINGS)


@pytest.fixture
def client(configured: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(configured)) as client:
        yield client


def finished(configured: Settings, task: str = "day_sheet", initiator: str = "windows:test",
             page: str | None = PAGE) -> str:
    store = Storage(configured.database_path)
    try:
        run_id = store.start_run(task, initiator, "fake")
        store.finish_run(run_id, Outcome.SUCCEEDED, Coverage.COMPLETE, "Synthetic", detail={})
    finally:
        store.close()
    if page is not None:
        path = scripts.printable_path(configured, run_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(page, encoding="utf-8")
    return run_id


async def test_a_printable_result_is_saved_beside_its_audit_not_in_it(
        configured: Settings) -> None:
    async def operation(_run_id: str) -> scripts.Result:
        return scripts.Result(summary="Synthetic", detail={}, coverage=Coverage.COMPLETE,
                              printable=PAGE)

    run_id = await scripts.recorded(configured, draft(), "day_sheet", operation)

    assert scripts.printable_path(configured, run_id).read_text(encoding="utf-8") == PAGE
    audit = (configured.data_dir / "audits" / f"{run_id}.jsonl").read_text(encoding="utf-8")
    finished_event = json.loads(audit.splitlines()[-1])
    assert "printable" not in finished_event["result"]


def test_the_run_page_offers_print_only_when_there_is_a_page(
        configured: Settings, client: TestClient) -> None:
    printable = finished(configured)
    plain = finished(configured, page=None)

    assert 'data-automation-id="run-print"' in client.get(f"/runs/{printable}").text
    assert 'data-automation-id="run-print"' not in client.get(f"/runs/{plain}").text
    assert client.get(f"/runs/{plain}/print").status_code == 404


def test_the_page_is_served_with_no_scripts_allowed(
        configured: Settings, client: TestClient) -> None:
    response = client.get(f"/runs/{finished(configured)}/print")

    assert response.status_code == 200
    assert response.text == PAGE
    policy = response.headers["content-security-policy"]
    assert "default-src 'none'" in policy and "script-src" not in policy


def test_a_draft_prints_for_its_owner_only(
        configured: Settings, client: TestClient) -> None:
    own = finished(configured, task="draft:synthetic", initiator=FAKE_STAFF)
    foreign = finished(configured, task="draft:synthetic", initiator="other@fake.invalid")
    assert client.get(f"/runs/{own}/print").status_code == 200
    assert client.get(f"/runs/{foreign}/print").status_code == 404
