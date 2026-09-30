"""What Principle's paging actually does, asserted against staging.

Every behaviour here contradicts the published specification, and each was found by the
recorder rather than by reading the document. They are pinned as tests so the fake can be
built on them and so a change at Principle's end is a test failure rather than a wrong report.

If one of these starts failing, Principle has changed and tests/fake/store.py must follow.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from principle_admin.config import Settings
from principle_admin.principle import PrincipleClient

pytestmark = pytest.mark.integration

WIDE_WINDOW_DAYS = 90


def _window(days: int = WIDE_WINDOW_DAYS) -> dict[str, str]:
    now = datetime.now(tz=UTC)
    return {
        "from": (now - timedelta(days=days)).isoformat().replace("+00:00", "Z"),
        "to": (now + timedelta(days=days)).isoformat().replace("+00:00", "Z"),
    }


async def test_total_is_the_page_size_not_the_result_size(
    staging_client: PrincipleClient, staging_settings: Settings
) -> None:
    """`meta.total` counts this page, not the result set. The spec says otherwise.

    A report that printed "N appointments" from `total` would print the page size. This is
    od_data's "unreliable grand totals" observation, now precisely characterised: asking for
    one row answers total 1 over the same window that answers total 20 at limit 20.
    """
    query = {"practiceId": staging_settings.practice_id, **_window()}
    one = await staging_client.get("list_appointments", query={**query, "limit": 1})
    twenty = await staging_client.get("list_appointments", query={**query, "limit": 20})

    assert one["meta"]["total"] == len(one["data"]) == 1
    assert twenty["meta"]["total"] == len(twenty["data"]) == 20
    assert one["meta"]["total"] != twenty["meta"]["total"], (
        "total varies with limit, so it is a page count; if this now holds steady Principle "
        "may have started returning a real total and the fake should follow"
    )


async def test_next_offset_id_is_the_last_row_created_at(
    staging_client: PrincipleClient, staging_settings: Settings
) -> None:
    """The cursor is a `createdAt`, not a record id, and the order is createdAt descending.

    The spec calls it "id of the last record to offset". A fake built on that would page a
    different sequence from the real API, and a report reading the second page would silently
    describe different appointments.
    """
    page = await staging_client.get(
        "list_appointments",
        query={"practiceId": staging_settings.practice_id, "limit": 3, **_window()},
    )
    rows = page["data"]
    assert len(rows) == 3
    assert page["meta"]["nextOffsetId"] == rows[-1]["createdAt"]

    created = [row["createdAt"] for row in rows]
    assert created == sorted(created, reverse=True), "expected createdAt descending"


async def test_an_unplaceable_offset_id_is_ignored_not_refused(
    staging_client: PrincipleClient, staging_settings: Settings
) -> None:
    """A cursor Principle cannot parse silently restarts the walk at page one.

    This is why PrincipleClient.rows refuses on a repeated row id: without that guard a stale
    cursor mid-walk would yield the first page twice and inflate every count in the report.
    """
    query = {"practiceId": staging_settings.practice_id, "limit": 2, **_window()}
    first = await staging_client.get("list_appointments", query=query)
    with_nonsense = await staging_client.get(
        "list_appointments", query={**query, "offsetId": "no-such-record"}
    )
    assert [row["id"] for row in with_nonsense["data"]] == [row["id"] for row in first["data"]]


async def test_the_server_default_limit_is_twenty(
    staging_client: PrincipleClient, staging_settings: Settings
) -> None:
    """Omitting `limit` gets 20 rows, not everything and not 100.

    Any code reading a single page without asking for a limit silently sees only 20
    appointments, which for a busy practice is most of a day missing.
    """
    page = await staging_client.get(
        "list_appointments", query={"practiceId": staging_settings.practice_id, **_window()}
    )
    assert page["meta"]["limit"] == 20


async def test_practitioners_is_not_paginated(
    staging_client: PrincipleClient, staging_settings: Settings
) -> None:
    """The practitioner listing carries no `meta` and ignores `limit`.

    The catalogue must not declare paging parameters here: sending `limit=1` returns every
    practitioner anyway, so a caller that believed the filter worked would be wrong about
    what it asked for.
    """
    envelope = await staging_client.get(
        "list_practitioners", path_params={"practice_id": staging_settings.practice_id}
    )
    assert "meta" not in envelope
    assert envelope["data"], "the practice must have at least one practitioner"


async def test_practices_is_not_paginated(staging_client: PrincipleClient) -> None:
    """`/v1/practices` returns `data` alone, which is what the catalogue assumes."""
    envelope = await staging_client.get("list_practices")
    assert "meta" not in envelope


async def test_a_full_walk_yields_no_duplicates(
    staging_client: PrincipleClient, staging_settings: Settings
) -> None:
    """The guarded walk must complete over a real multi-page result set.

    Exercises the client's two cursor guards against the real cursor rather than a mock. The
    window and page size are kept small on purpose: a wide window at page size 3 is hundreds
    of requests against someone else's API to prove the same property.
    """
    rows = [
        row
        async for row in staging_client.rows(
            "list_appointments",
            query={"practiceId": staging_settings.practice_id, **_window(days=14)},
            page_size=10,
        )
    ]
    ids = [row["id"] for row in rows]
    assert len(ids) > 10, "the window must span more than one page for this to prove anything"
    assert len(ids) == len(set(ids))
