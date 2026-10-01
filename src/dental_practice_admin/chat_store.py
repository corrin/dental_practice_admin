"""ChatKit's `Store` contract over SQLite.

Two tables, each holding the SDK's own JSON payload plus the columns we need to find, scope and
order rows. Storing the payload whole is deliberate: ChatKit's item types are a discriminated
union that grows with the SDK, and a schema of our own would turn every SDK release into a
migration and would quietly drop the fields we had not thought to model.

Round-tripping goes through the SDK's validators (`ThreadMetadata.model_validate`,
`TypeAdapter(ThreadItem).validate_python`), so a payload we cannot reconstruct fails loudly here
rather than halfway through a conversation.

Run records live in `storage.py` and are a different thing. They are not a substitute for
conversation items, and conflating them would lose most of each message.

Every operation is scoped to the signed-in staff member's address, and the scope is checked on
the *parent thread* for item operations, not taken from the item. One member of staff must not
be able to read another's conversation by guessing a thread id.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypeVar

from chatkit.store import Store
from chatkit.types import Page, ThreadItem, ThreadMetadata
from pydantic import TypeAdapter

from dental_practice_admin.auth import StaffUser

SCHEMA = """
CREATE TABLE IF NOT EXISTS chat_threads (
    thread_id   TEXT PRIMARY KEY,
    staff_email TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    payload     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS chat_threads_by_staff
    ON chat_threads(staff_email, created_at DESC, thread_id);

CREATE TABLE IF NOT EXISTS chat_items (
    item_id    TEXT PRIMARY KEY,
    thread_id  TEXT NOT NULL REFERENCES chat_threads(thread_id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    payload    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS chat_items_by_thread ON chat_items(thread_id, created_at, item_id);
"""

ITEM_ADAPTER: TypeAdapter[ThreadItem] = TypeAdapter(ThreadItem)

T = TypeVar("T")


class ThreadNotFoundError(Exception):
    """No such thread for this member of staff.

    Deliberately identical whether the thread is absent or belongs to someone else: a
    distinguishable "exists but not yours" tells a caller which ids are real.
    """

    def __init__(self, thread_id: str) -> None:
        super().__init__(f"no thread {thread_id!r}")


def storage_timestamp(value: datetime | None) -> str:
    """An indexable, comparable timestamp for a value the SDK may hand over naive.

    ChatKit has been observed emitting process-local naive datetimes. Ordering rows by those
    across a daylight-saving boundary reorders a conversation, so the column is always UTC.
    The SDK's own payload is stored untouched -- its duration arithmetic depends on the value it
    produced.
    """
    if value is None:
        return datetime.now(tz=UTC).isoformat()
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()


class SqliteChatStore(Store[StaffUser]):
    """Threads and items for one practice, scoped by staff address."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, isolation_level=None, timeout=15.0)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA busy_timeout=15000")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript(SCHEMA)

    def close(self) -> None:
        self.db.close()

    # -- threads -------------------------------------------------------------

    async def load_thread(self, thread_id: str, context: StaffUser) -> ThreadMetadata:
        return ThreadMetadata.model_validate(json.loads(self._thread_row(thread_id, context)))

    async def save_thread(self, thread: ThreadMetadata, context: StaffUser) -> None:
        self.db.execute(
            "INSERT INTO chat_threads (thread_id, staff_email, created_at, payload)"
            " VALUES (:thread_id, :staff_email, :created_at, :payload)"
            " ON CONFLICT(thread_id) DO UPDATE SET payload = :payload"
            " WHERE staff_email = :staff_email",
            {
                "thread_id": thread.id,
                "staff_email": context.email,
                "created_at": storage_timestamp(getattr(thread, "created_at", None)),
                "payload": _dump(thread),
            },
        )

    async def load_threads(
        self, limit: int, after: str | None, order: str, context: StaffUser
    ) -> Page[ThreadMetadata]:
        rows = self._page(
            table="chat_threads",
            key="thread_id",
            where="staff_email = :staff_email",
            params={"staff_email": context.email},
            after=after,
            limit=limit,
            order=order,
        )
        return _page(
            [ThreadMetadata.model_validate(json.loads(row["payload"])) for row in rows],
            rows,
            "thread_id",
            limit,
        )

    async def delete_thread(self, thread_id: str, context: StaffUser) -> None:
        self._thread_row(thread_id, context)
        # Items go with it: ON DELETE CASCADE, with foreign_keys=ON set above.
        self.db.execute("DELETE FROM chat_threads WHERE thread_id = ?", (thread_id,))

    # -- items ---------------------------------------------------------------

    async def add_thread_item(self, thread_id: str, item: ThreadItem, context: StaffUser) -> None:
        await self.save_item(thread_id, item, context)

    async def save_item(self, thread_id: str, item: ThreadItem, context: StaffUser) -> None:
        self._thread_row(thread_id, context)
        if getattr(item, "thread_id", thread_id) != thread_id:
            raise ThreadNotFoundError(thread_id)
        self.db.execute(
            "INSERT INTO chat_items (item_id, thread_id, created_at, payload)"
            " VALUES (:item_id, :thread_id, :created_at, :payload)"
            " ON CONFLICT(item_id) DO UPDATE SET payload = :payload",
            {
                "item_id": item.id,
                "thread_id": thread_id,
                "created_at": storage_timestamp(getattr(item, "created_at", None)),
                "payload": _dump(item),
            },
        )

    async def load_item(self, thread_id: str, item_id: str, context: StaffUser) -> ThreadItem:
        self._thread_row(thread_id, context)
        row = self.db.execute(
            "SELECT payload FROM chat_items WHERE thread_id = ? AND item_id = ?",
            (thread_id, item_id),
        ).fetchone()
        if row is None:
            raise ThreadNotFoundError(f"{thread_id}/{item_id}")
        return ITEM_ADAPTER.validate_python(json.loads(row["payload"]))

    async def load_thread_items(
        self, thread_id: str, after: str | None, limit: int, order: str, context: StaffUser
    ) -> Page[ThreadItem]:
        self._thread_row(thread_id, context)
        rows = self._page(
            table="chat_items",
            key="item_id",
            where="thread_id = :thread_id",
            params={"thread_id": thread_id},
            after=after,
            limit=limit,
            order=order,
        )
        return _page(
            [ITEM_ADAPTER.validate_python(json.loads(row["payload"])) for row in rows],
            rows,
            "item_id",
            limit,
        )

    async def delete_thread_item(
        self, thread_id: str, item_id: str, context: StaffUser
    ) -> None:
        self._thread_row(thread_id, context)
        self.db.execute(
            "DELETE FROM chat_items WHERE thread_id = ? AND item_id = ?", (thread_id, item_id)
        )

    # -- attachments ---------------------------------------------------------

    async def save_attachment(self, attachment: Any, context: StaffUser) -> None:
        raise NotImplementedError(_NO_UPLOADS)

    async def load_attachment(self, attachment_id: str, context: StaffUser) -> Any:
        raise NotImplementedError(_NO_UPLOADS)

    async def delete_attachment(self, attachment_id: str, context: StaffUser) -> None:
        raise NotImplementedError(_NO_UPLOADS)

    # -- internals -----------------------------------------------------------

    def _thread_row(self, thread_id: str, context: StaffUser) -> str:
        """The thread's payload, or a refusal. The only place scope is enforced."""
        row = self.db.execute(
            "SELECT payload FROM chat_threads WHERE thread_id = ? AND staff_email = ?",
            (thread_id, context.email),
        ).fetchone()
        if row is None:
            raise ThreadNotFoundError(thread_id)
        return str(row["payload"])

    def _page(
        self,
        table: str,
        key: str,
        where: str,
        params: dict[str, object],
        after: str | None,
        limit: int,
        order: str,
    ) -> list[sqlite3.Row]:
        """One page, ordered by timestamp with the id as tie-breaker.

        Rows created in the same instant are common -- a user message and the assistant's reply
        can share a timestamp -- and ordering by time alone makes their order arbitrary, so a
        cursor could skip one or repeat it. `limit + 1` rows are fetched so the caller can tell
        whether more remain without a second count.
        """
        descending = order == "desc"
        comparison = "<" if descending else ">"
        direction = "DESC" if descending else "ASC"
        clause = where
        if after is not None:
            clause = (
                f"{where} AND (created_at, {key}) {comparison}"
                f" (SELECT created_at, {key} FROM {table} WHERE {key} = :after)"
            )
            params = dict(params) | {"after": after}
        return list(
            self.db.execute(
                f"SELECT * FROM {table} WHERE {clause}"
                f" ORDER BY created_at {direction}, {key} {direction} LIMIT :limit",
                dict(params) | {"limit": limit + 1},
            ).fetchall()
        )


_NO_UPLOADS = (
    "attachments are not enabled: staff chat reads Principle and does not accept files, and a"
    " silent success here would lose whatever was uploaded"
)


def _dump(model: Any) -> str:
    return json.dumps(model.model_dump(mode="json"))


def _page(items: list[T], rows: list[sqlite3.Row], key: str, limit: int) -> Page[T]:
    """Trim the extra row fetched to detect `has_more`, and name the next cursor."""
    has_more = len(rows) > limit
    kept = items[:limit]
    return Page(
        data=kept,
        has_more=has_more,
        after=str(rows[limit - 1][key]) if has_more and kept else None,
    )
