"""使用 SQLite 保存 client_request_id 占用状态与完整 SSE 回放。"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from contextlib import closing
from pathlib import Path

from ...application.contracts import AgentEvent
from ...application.idempotency import RequestReservation


class SQLiteRequestReplayStore:
    """单实例请求幂等存储，原子区分首次执行、回放、处理中和冲突。"""

    def __init__(
        self,
        path: str | Path,
        processing_timeout_seconds: int = 60,
        retention_seconds: int = 86_400,
    ) -> None:
        self.path = Path(path)
        self.processing_timeout_seconds = processing_timeout_seconds
        self.retention_seconds = retention_seconds
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS request_replays (
                    tenant_id TEXT NOT NULL,
                    client_request_id TEXT NOT NULL,
                    fingerprint TEXT NOT NULL,
                    request_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    events TEXT,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY (tenant_id, client_request_id)
                )
                """
            )

    async def reserve(
        self,
        tenant_id: str,
        client_request_id: str,
        fingerprint: str,
        request_id: str,
    ) -> RequestReservation:
        return await asyncio.to_thread(
            self._reserve, tenant_id, client_request_id, fingerprint, request_id
        )

    def _reserve(
        self,
        tenant_id: str,
        client_request_id: str,
        fingerprint: str,
        request_id: str,
    ) -> RequestReservation:
        now = time.time()
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            # 每次占用顺带小批量清理过期终态，避免单机数据库无限增长。
            connection.execute(
                """DELETE FROM request_replays
                   WHERE status IN ('completed', 'failed') AND updated_at < ?""",
                (now - self.retention_seconds,),
            )
            row = connection.execute(
                """SELECT * FROM request_replays
                   WHERE tenant_id = ? AND client_request_id = ?""",
                (tenant_id, client_request_id),
            ).fetchone()
            if row is None:
                connection.execute(
                    """INSERT INTO request_replays
                       VALUES (?, ?, ?, ?, 'processing', NULL, ?)""",
                    (tenant_id, client_request_id, fingerprint, request_id, now),
                )
                connection.commit()
                return RequestReservation("execute", request_id)
            if row["fingerprint"] != fingerprint:
                connection.rollback()
                return RequestReservation("conflict", str(row["request_id"]))
            if row["status"] == "completed" and row["events"]:
                events = tuple(
                    AgentEvent(
                        event=entry["event"],
                        request_id=entry["request_id"],
                        data=dict(entry.get("data", {})),
                    )
                    for entry in json.loads(str(row["events"]))
                )
                connection.rollback()
                return RequestReservation("replay", str(row["request_id"]), events)
            is_stale = now - float(row["updated_at"]) >= self.processing_timeout_seconds
            if row["status"] == "failed" or is_stale:
                # 上次流被中断或进程异常退出时允许新的所有者接管。
                connection.execute(
                    """UPDATE request_replays
                       SET request_id = ?, status = 'processing', events = NULL, updated_at = ?
                       WHERE tenant_id = ? AND client_request_id = ?""",
                    (request_id, now, tenant_id, client_request_id),
                )
                connection.commit()
                return RequestReservation("execute", request_id)
            connection.rollback()
            return RequestReservation("in_progress", str(row["request_id"]))

    async def complete(
        self,
        tenant_id: str,
        client_request_id: str,
        request_id: str,
        events: tuple[AgentEvent, ...],
    ) -> None:
        payload = json.dumps(
            [event.as_dict() for event in events],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        await asyncio.to_thread(
            self._set_status,
            tenant_id,
            client_request_id,
            request_id,
            "completed",
            payload,
        )

    async def release(
        self,
        tenant_id: str,
        client_request_id: str,
        request_id: str,
    ) -> None:
        await asyncio.to_thread(
            self._set_status,
            tenant_id,
            client_request_id,
            request_id,
            "failed",
            None,
        )

    def _set_status(
        self,
        tenant_id: str,
        client_request_id: str,
        request_id: str,
        status: str,
        events: str | None,
    ) -> None:
        with closing(self._connect()) as connection, connection:
            cursor = connection.execute(
                """UPDATE request_replays SET status = ?, events = ?, updated_at = ?
                   WHERE tenant_id = ? AND client_request_id = ? AND request_id = ?""",
                (
                    status,
                    events,
                    time.time(),
                    tenant_id,
                    client_request_id,
                    request_id,
                ),
            )
            if cursor.rowcount == 0:
                raise RuntimeError("request replay ownership lost")
