"""Staging probe boundaries without credentials or network access."""

import json
from unittest.mock import MagicMock

import pytest
from scripts.check_staging import DOCUMENT_PREFIX, StagingBrowser, document_names


def test_document_names_preserve_punctuation_in_webchannel_frames() -> None:
    name = DOCUMENT_PREFIX + "organisations/example.test@example.invalid"
    payload = json.dumps([[0, json.dumps({"documentDelete": {"document": name}})]])
    assert document_names("123\n" + payload) == {name}


@pytest.mark.parametrize("name", [
    "projects/production/databases/(default)/documents/users/example",
    DOCUMENT_PREFIX + "../users/example",
    DOCUMENT_PREFIX + "users/example?key=secret",
    DOCUMENT_PREFIX + "users/%2e%2e",
])
def test_document_read_refuses_other_projects_and_url_manipulation(name: str) -> None:
    session = StagingBrowser(MagicMock())
    with pytest.raises(ValueError):
        session.read_document(name)
