"""使用 SQLite 持久化短期 Session 状态、意图和消息。"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import Callable

from ...application.session import (
    SessionIdentityConflict,
    SessionIntentSnapshot,
    SessionMessage,
    SessionState,
    SessionVersionConflict,
)


class SQLiteSessionStore:
    """带 TTL、消息裁剪和乐观版本检查的单实例 Session Store。"""

    def __init__(
        self,
        path: str | Path,
        ttl_seconds: int = 86_400,
        max_messages: int = 40,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        if max_messages < 2:
            raise ValueError("max_messages must be at least 2")
        self.path = Path(path)
        self.ttl_seconds = ttl_seconds
        self.max_messages = max_messages
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
                CREATE TABLE IF NOT EXISTS sessions (
                    tenant_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    user_id TEXT,
                    messages TEXT NOT NULL,
                    current_intent TEXT,
                    version INTEGER NOT NULL,
                    updated_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    PRIMARY KEY (tenant_id, session_id)
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_sessions_expiry ON sessions(expires_at)"
            )

    async def get_or_create(
        self, session_id: str, tenant_id: str, user_id: str | None
    ) -> SessionState:
        return await asyncio.to_thread(
            self._get_or_create, session_id, tenant_id, user_id
        )

    def _get_or_create(
        self, session_id: str, tenant_id: str, user_id: str | None
    ) -> SessionState:
        now = self.clock()
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._delete_expired(connection, now)
            row = self._select(connection, tenant_id, session_id)
            if row is None:
                connection.execute(
                    """INSERT INTO sessions
                       VALUES (?, ?, ?, '[]', NULL, 0, ?, ?)""",
                    (
                        tenant_id,
                        session_id,
                        user_id,
                        now,
                        now + self.ttl_seconds,
                    ),
                )
                connection.commit()
                return SessionState(session_id, tenant_id, user_id)
            if row["user_id"] != user_id:
                connection.rollback()
                raise SessionIdentityConflict(
                    "session already belongs to a different user identity"
                )
            connection.commit()
            return self._row_to_state(row)

    async def append(self, state: SessionState, message: SessionMessage) -> None:
        messages = [*state.messages, message][-self.max_messages:]
        await asyncio.to_thread(
            self._update,
            state,
            messages,
            state.current_intent,
        )
        state.messages = messages
        state.version += 1

    async def set_intent(
        self, state: SessionState, snapshot: SessionIntentSnapshot
    ) -> None:
        await asyncio.to_thread(self._update, state, state.messages, snapshot)
        state.current_intent = snapshot
        state.version += 1

    def _update(
        self,
        state: SessionState,
        messages: list[SessionMessage],
        intent: SessionIntentSnapshot | None,
    ) -> None:
        now = self.clock()
        messages_json = json.dumps(
            [message.__dict__ for message in messages],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        intent_json = (
            json.dumps(intent.__dict__, ensure_ascii=False, separators=(",", ":"))
            if intent is not None
            else None
        )
        with closing(self._connect()) as connection, connection:
            cursor = connection.execute(
                """UPDATE sessions
                   SET messages = ?, current_intent = ?, version = version + 1,
                       updated_at = ?, expires_at = ?
                   WHERE tenant_id = ? AND session_id = ? AND user_id IS ?
                         AND version = ? AND expires_at > ?""",
                (
                    messages_json,
                    intent_json,
                    now,
                    now + self.ttl_seconds,
                    state.tenant_id,
                    state.session_id,
                    state.user_id,
                    state.version,
                    now,
                ),
            )
            if cursor.rowcount == 0:
                raise SessionVersionConflict("session snapshot is stale or expired")

    async def get(self, tenant_id: str, session_id: str) -> SessionState | None:
        return await asyncio.to_thread(self._get, tenant_id, session_id)

    def _get(self, tenant_id: str, session_id: str) -> SessionState | None:
        now = self.clock()
        with closing(self._connect()) as connection, connection:
            self._delete_expired(connection, now)
            row = self._select(connection, tenant_id, session_id)
        return self._row_to_state(row) if row is not None else None

    @staticmethod
    def _select(
        connection: sqlite3.Connection, tenant_id: str, session_id: str
    ) -> sqlite3.Row | None:
        return connection.execute(
            "SELECT * FROM sessions WHERE tenant_id = ? AND session_id = ?",
            (tenant_id, session_id),
        ).fetchone()

    @staticmethod
    def _delete_expired(connection: sqlite3.Connection, now: float) -> None:
        connection.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,))

    @staticmethod
    def _row_to_state(row: sqlite3.Row) -> SessionState:
        messages = [SessionMessage(**entry) for entry in json.loads(row["messages"])]
        raw_intent = json.loads(row["current_intent"]) if row["current_intent"] else None
        intent = SessionIntentSnapshot(**raw_intent) if raw_intent is not None else None
        return SessionState(
            session_id=str(row["session_id"]),
            tenant_id=str(row["tenant_id"]),
            user_id=row["user_id"],
            messages=messages,
            current_intent=intent,
            version=int(row["version"]),
        )
