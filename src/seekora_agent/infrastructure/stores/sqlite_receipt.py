"""使用 SQLite 持久化完整 Recommendation Receipt。"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import Callable

from ...application.receipt import RecommendationReceipt, ToolCallReceipt


class SQLiteReceiptStore:
    """支持跨重启读取、索引和保留期清理的单实例 Receipt Store。"""

    def __init__(
        self,
        path: str | Path,
        retention_seconds: int = 30 * 86_400,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if retention_seconds <= 0:
            raise ValueError("retention_seconds must be positive")
        self.path = Path(path)
        self.retention_seconds = retention_seconds
        self.clock = clock
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
                CREATE TABLE IF NOT EXISTS recommendation_receipts (
                    request_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    payload TEXT NOT NULL,
                    stored_at REAL NOT NULL
                )
                """
            )
            connection.execute(
                """CREATE INDEX IF NOT EXISTS idx_receipts_tenant_session
                   ON recommendation_receipts(tenant_id, session_id, started_at)"""
            )
            connection.execute(
                """CREATE INDEX IF NOT EXISTS idx_receipts_status
                   ON recommendation_receipts(status, stored_at)"""
            )

    async def put(self, receipt: RecommendationReceipt) -> None:
        await asyncio.to_thread(self._put, receipt)

    def _put(self, receipt: RecommendationReceipt) -> None:
        now = self.clock()
        payload = json.dumps(
            receipt.as_dict(), ensure_ascii=False, separators=(",", ":")
        )
        with closing(self._connect()) as connection, connection:
            self._purge(connection, now)
            connection.execute(
                """INSERT INTO recommendation_receipts
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(request_id) DO UPDATE SET
                       tenant_id = excluded.tenant_id,
                       session_id = excluded.session_id,
                       status = excluded.status,
                       started_at = excluded.started_at,
                       finished_at = excluded.finished_at,
                       payload = excluded.payload,
                       stored_at = excluded.stored_at""",
                (
                    receipt.request_id,
                    receipt.tenant_id,
                    receipt.session_id,
                    receipt.status,
                    receipt.started_at,
                    receipt.finished_at,
                    payload,
                    now,
                ),
            )

    async def get(self, request_id: str) -> RecommendationReceipt | None:
        return await asyncio.to_thread(self._get, request_id)

    def _get(self, request_id: str) -> RecommendationReceipt | None:
        now = self.clock()
        with closing(self._connect()) as connection, connection:
            self._purge(connection, now)
            row = connection.execute(
                "SELECT payload FROM recommendation_receipts WHERE request_id = ?",
                (request_id,),
            ).fetchone()
        if row is None:
            return None
        raw = json.loads(str(row["payload"]))
        raw["tool_calls"] = [ToolCallReceipt(**entry) for entry in raw["tool_calls"]]
        return RecommendationReceipt(**raw)

    def _purge(self, connection: sqlite3.Connection, now: float) -> None:
        connection.execute(
            "DELETE FROM recommendation_receipts WHERE stored_at <= ?",
            (now - self.retention_seconds,),
        )
