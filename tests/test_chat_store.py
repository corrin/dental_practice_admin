"""The ChatKit Store contract, against our SQLite implementation.

Written against the contract rather than the implementation: ARCHITECTURE.md warns specifically
against assuming a store satisfies ChatKit's interface, and the way that assumption fails is a
conversation that looks fine until the SDK reads it back.

Items are constructed with the SDK's own types, so a payload this suite round-trips is one
ChatKit can actually produce.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from chatkit.types import (
    ActiveStatus,
    AssistantMessageContent,
    AssistantMessageItem,
    InferenceOptions,
    ThreadMetadata,
    UserMessageItem,
    UserMessageTextContent,
)

from dental_practice_admin.auth import StaffUser
from dental_practice_admin.chat_store import SqliteChatStore, ThreadNotFoundError

NURSE = StaffUser(email="nurse@practice.nz", name="Nurse")
RECEPTION = StaffUser(email="reception@practice.nz", name="Reception")


@pytest.fixture
def store(tmp_path: Path) -> Iterator[SqliteChatStore]:
    opened = SqliteChatStore(tmp_path / "chat.db")
    yield opened
    opened.close()


def _thread(thread_id: str, at: datetime | None = None) -> ThreadMetadata:
    return ThreadMetadata(
        id=thread_id,
        created_at=at or datetime.now(tz=UTC),
        title=None,
        status=ActiveStatus(),
    )


def _user_item(item_id: str, thread_id: str, text: str, at: datetime) -> UserMessageItem:
    return UserMessageItem(
        id=item_id,
        thread_id=thread_id,
        created_at=at,
        content=[UserMessageTextContent(text=text)],
        attachments=[],
        quoted_text=None,
        inference_options=InferenceOptions(),
    )


def _assistant_item(
    item_id: str, thread_id: str, text: str, at: datetime
) -> AssistantMessageItem:
    return AssistantMessageItem(
        id=item_id,
        thread_id=thread_id,
        created_at=at,
        content=[AssistantMessageContent(text=text, annotations=[])],
    )


async def test_a_thread_round_trips_through_the_sdk_validators(
    store: SqliteChatStore,
) -> None:
    """A payload we cannot reconstruct must fail here, not mid-conversation.

    Storing our own reduced shape instead of the SDK's payload would pass this test's
    role-and-text expectations while dropping every field we had not thought to model.
    """
    await store.save_thread(_thread("t1"), NURSE)
    loaded = await store.load_thread("t1", NURSE)
    assert loaded.id == "t1"


async def test_items_come_back_in_order_with_content_intact(
    store: SqliteChatStore,
) -> None:
    """A reordering or a lossy round-trip would scramble the conversation the agent is given."""
    await store.save_thread(_thread("t1"), NURSE)
    base = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)
    await store.add_thread_item("t1", _user_item("i1", "t1", "How many tomorrow?", base), NURSE)
    await store.add_thread_item(
        "t1", _assistant_item("i2", "t1", "Eight booked.", base), NURSE
    )

    page = await store.load_thread_items("t1", after=None, limit=10, order="asc", context=NURSE)
    assert [item.id for item in page.data] == ["i1", "i2"]
    assert page.data[0].content[0].text == "How many tomorrow?"  # type: ignore[union-attr]


async def test_items_sharing_a_timestamp_still_page_deterministically(
    store: SqliteChatStore,
) -> None:
    """A question and its answer can share an instant, and a cursor must not skip or repeat.

    Ordering by timestamp alone makes their order arbitrary between queries, so paging could
    return one item twice and never return the other.
    """
    await store.save_thread(_thread("t1"), NURSE)
    same = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)
    for index in range(5):
        await store.add_thread_item(
            "t1", _user_item(f"i{index}", "t1", f"message {index}", same), NURSE
        )

    seen: list[str] = []
    after: str | None = None
    for _ in range(5):
        page = await store.load_thread_items(
            "t1", after=after, limit=2, order="asc", context=NURSE
        )
        seen.extend(item.id for item in page.data)
        if not page.has_more:
            break
        after = page.after
    assert sorted(seen) == ["i0", "i1", "i2", "i3", "i4"]
    assert len(seen) == len(set(seen)), "a page boundary repeated an item"


async def test_has_more_and_after_describe_the_next_page(store: SqliteChatStore) -> None:
    """`has_more` must be false on the last page, or a client pages forever."""
    await store.save_thread(_thread("t1"), NURSE)
    base = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)
    for index in range(3):
        await store.add_thread_item(
            "t1", _user_item(f"i{index}", "t1", "x", base), NURSE
        )
    full = await store.load_thread_items("t1", after=None, limit=3, order="asc", context=NURSE)
    assert full.has_more is False
    assert full.after is None


async def test_one_member_of_staff_cannot_read_another_s_thread(
    store: SqliteChatStore,
) -> None:
    """Conversation isolation, which ARCHITECTURE.md requires.

    Thread ids are short strings the SDK generates; if scope were taken from the item rather
    than checked against the parent thread, guessing one would expose a colleague's chat.
    """
    await store.save_thread(_thread("t1"), NURSE)
    with pytest.raises(ThreadNotFoundError):
        await store.load_thread("t1", RECEPTION)
    with pytest.raises(ThreadNotFoundError):
        await store.load_thread_items(
            "t1", after=None, limit=10, order="asc", context=RECEPTION
        )


async def test_another_member_of_staff_cannot_write_into_a_thread(
    store: SqliteChatStore,
) -> None:
    """Writing is checked on the parent thread too, not just reading."""
    await store.save_thread(_thread("t1"), NURSE)
    base = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)
    with pytest.raises(ThreadNotFoundError):
        await store.add_thread_item(
            "t1", _user_item("i1", "t1", "let me in", base), RECEPTION
        )


async def test_an_item_naming_a_different_thread_is_refused(
    store: SqliteChatStore,
) -> None:
    """The payload's own thread id must agree with the thread it is being saved into.

    Trusting the argument alone would let an item addressed to someone else's thread be filed
    under ours, or the reverse, depending on which value a later reader believed.
    """
    await store.save_thread(_thread("t1"), NURSE)
    await store.save_thread(_thread("t2"), NURSE)
    base = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)
    with pytest.raises(ThreadNotFoundError):
        await store.save_item("t1", _user_item("i1", "t2", "misfiled", base), NURSE)


async def test_listing_threads_shows_only_your_own(store: SqliteChatStore) -> None:
    """The thread list is per-person; a shared list would leak subject lines at minimum."""
    await store.save_thread(_thread("mine"), NURSE)
    await store.save_thread(_thread("theirs"), RECEPTION)
    page = await store.load_threads(limit=10, after=None, order="desc", context=NURSE)
    assert [thread.id for thread in page.data] == ["mine"]


async def test_deleting_a_thread_takes_its_items(store: SqliteChatStore) -> None:
    """Orphaned items would be unreachable rows holding conversation content indefinitely."""
    await store.save_thread(_thread("t1"), NURSE)
    base = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)
    await store.add_thread_item("t1", _user_item("i1", "t1", "x", base), NURSE)
    await store.delete_thread("t1", NURSE)
    remaining = store.db.execute("SELECT count(*) FROM chat_items").fetchone()[0]
    assert remaining == 0


async def test_a_naive_timestamp_is_stored_as_utc(store: SqliteChatStore) -> None:
    """ChatKit has been seen emitting naive local datetimes.

    Indexing those directly reorders a conversation across a daylight-saving boundary, so the
    column is always UTC while the SDK payload keeps whatever the SDK produced.
    """
    await store.save_thread(_thread("t1"), NURSE)
    naive = datetime(2026, 10, 1, 9, 0)
    await store.add_thread_item("t1", _user_item("i1", "t1", "x", naive), NURSE)
    stored = store.db.execute("SELECT created_at FROM chat_items").fetchone()[0]
    assert stored.endswith("+00:00")


async def test_attachments_are_refused_rather_than_silently_dropped(
    store: SqliteChatStore,
) -> None:
    """Uploads are not enabled, and a silent success would lose the file.

    A staff member who attached a scan and saw no error would reasonably assume it arrived.
    """
    with pytest.raises(NotImplementedError, match="not enabled"):
        await store.save_attachment(object(), NURSE)


async def test_generated_ids_are_distinct(store: SqliteChatStore) -> None:
    """Inherited from the SDK, and asserted because a collision would overwrite a message."""
    ids = {store.generate_item_id("message", _thread("t1"), NURSE) for _ in range(50)}
    assert len(ids) == 50
