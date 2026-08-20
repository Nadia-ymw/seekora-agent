"""使用 SQLite 实现的本地持久化行为事件队列。"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from contextlib import closing
from pathlib import Path

from ...application.event_pipeline import QueueEventConflict
from ...domain.behavior import BehaviorEvent
from ...domain.event_pipeline import QueuedBehaviorEvent


class SQLiteBehaviorEventQueue:
    """单机持久化队列；数据库仅在首次事件操作时延迟创建。"""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = asyncio.Lock()
        self._initialized = False

    async def enqueue(
        self, event: BehaviorEvent, late: bool
    ) -> tuple[QueuedBehaviorEvent, bool]:
        async with self._lock:
            return await asyncio.to_thread(self._enqueue_sync, event, late)

    async def get(
        self, tenant_id: str, event_id: str
    ) -> QueuedBehaviorEvent | None:
        async with self._lock:
            return await asyncio.to_thread(self._get_sync, tenant_id, event_id)

    async def mark_processed(self, tenant_id: str, event_id: str) -> None:
        await self._update_status(tenant_id, event_id, "processed", None, True)

    async def mark_failed(self, tenant_id: str, event_id: str, error: str) -> None:
        await self._update_status(tenant_id, event_id, "failed", error, True)

    async def requeue(self, tenant_id: str, event_id: str) -> QueuedBehaviorEvent:
        await self._update_status(tenant_id, event_id, "pending", None, False)
        entry = await self.get(tenant_id, event_id)
        if entry is None:
            raise KeyError("queued event not found")
        return entry

    async def delete_by_user(self, tenant_id: str, user_id: str) -> int:
        async with self._lock:
            return await asyncio.to_thread(self._delete_by_user_sync, tenant_id, user_id)

    async def _update_status(
        self,
        tenant_id: str,
        event_id: str,
        status: str,
        error: str | None,
        increment_attempts: bool,
    ) -> None:
        async with self._lock:
            await asyncio.to_thread(
                self._update_status_sync,
                tenant_id,
                event_id,
                status,
                error,
                increment_attempts,
            )

    def _connect(self) -> sqlite3.Connection:
        if not self._initialized:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        if not self._initialized:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS behavior_event_queue (
                    tenant_id TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    late INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT,
                    PRIMARY KEY (tenant_id, event_id)
                )
            """)
            connection.commit()
            self._initialized = True
        return connection

    def _enqueue_sync(
        self, event: BehaviorEvent, late: bool
    ) -> tuple[QueuedBehaviorEvent, bool]:
        payload = json.dumps(event.as_dict(), ensure_ascii=False, separators=(",", ":"))
        with closing(self._connect()) as connection, connection:
            row = connection.execute(
                "SELECT * FROM behavior_event_queue WHERE tenant_id=? AND event_id=?",
                (event.tenant_id, event.event_id),
            ).fetchone()
            if row is not None:
                existing = self._row_to_entry(row)
                if existing.event.idempotency_payload() != event.idempotency_payload():
                    raise QueueEventConflict(
                        "event_id already queued with different payload"
                    )
                return existing, True
            connection.execute(
                """INSERT INTO behavior_event_queue
                   (tenant_id,event_id,user_id,payload,late,status,attempts,last_error)
                   VALUES (?,?,?,?,?,'pending',0,NULL)""",
                (event.tenant_id, event.event_id, event.user_id, payload, int(late)),
            )
        return QueuedBehaviorEvent(event=event, late=late), False

    def _get_sync(self, tenant_id: str, event_id: str) -> QueuedBehaviorEvent | None:
        with closing(self._connect()) as connection, connection:
            row = connection.execute(
                "SELECT * FROM behavior_event_queue WHERE tenant_id=? AND event_id=?",
                (tenant_id, event_id),
            ).fetchone()
        return self._row_to_entry(row) if row is not None else None

    def _update_status_sync(
        self,
        tenant_id: str,
        event_id: str,
        status: str,
        error: str | None,
        increment_attempts: bool,
    ) -> None:
        increment = 1 if increment_attempts else 0
        with closing(self._connect()) as connection, connection:
            cursor = connection.execute(
                """UPDATE behavior_event_queue
                   SET status=?, last_error=?, attempts=attempts+?
                   WHERE tenant_id=? AND event_id=?""",
                (status, error, increment, tenant_id, event_id),
            )
            if cursor.rowcount == 0:
                raise KeyError("queued event not found")

    def _delete_by_user_sync(self, tenant_id: str, user_id: str) -> int:
        with closing(self._connect()) as connection, connection:
            cursor = connection.execute(
                "DELETE FROM behavior_event_queue WHERE tenant_id=? AND user_id=?",
                (tenant_id, user_id),
            )
            return cursor.rowcount

    @staticmethod
    def _row_to_entry(row: sqlite3.Row) -> QueuedBehaviorEvent:
        payload = json.loads(str(row["payload"]))
        payload["recall_sources"] = tuple(payload.get("recall_sources", []))
        return QueuedBehaviorEvent(
            event=BehaviorEvent(**payload),
            late=bool(row["late"]),
            status=str(row["status"]),
            attempts=int(row["attempts"]),
            last_error=row["last_error"],
        )
