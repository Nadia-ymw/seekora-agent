"""使用 SQLite 持久化经用户明确授权的长期画像。"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from collections.abc import Iterable
from contextlib import closing
from pathlib import Path

from ...domain.profile import ConsentState, LongTermProfile


class SQLiteProfileStore:
    """单实例长期画像存储，以租户和用户联合键保证数据隔离。"""

    def __init__(
        self,
        path: str | Path,
        initial_profiles: Iterable[LongTermProfile] = (),
    ) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize(tuple(initial_profiles))

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self, initial_profiles: tuple[LongTermProfile, ...]) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS long_term_profiles (
                    tenant_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    positive_preferences TEXT NOT NULL,
                    negative_preferences TEXT NOT NULL,
                    personalization_enabled INTEGER NOT NULL,
                    behavior_storage_enabled INTEGER NOT NULL,
                    consent_updated_at TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (tenant_id, user_id)
                )
                """
            )
            # 开发测试账户只在首次启动时写入，不能覆盖用户后续修改的授权。
            for profile in initial_profiles:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO long_term_profiles VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    self._parameters(profile),
                )

    @staticmethod
    def _parameters(profile: LongTermProfile) -> tuple[object, ...]:
        return (
            profile.tenant_id,
            profile.user_id,
            json.dumps(profile.positive_preferences, ensure_ascii=False),
            json.dumps(profile.negative_preferences, ensure_ascii=False),
            int(profile.consent.personalization_enabled),
            int(profile.consent.behavior_storage_enabled),
            profile.consent.updated_at,
            profile.version,
            profile.updated_at,
        )

    async def get(self, tenant_id: str, user_id: str) -> LongTermProfile | None:
        return await asyncio.to_thread(self._get, tenant_id, user_id)

    def _get(self, tenant_id: str, user_id: str) -> LongTermProfile | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM long_term_profiles WHERE tenant_id = ? AND user_id = ?",
                (tenant_id, user_id),
            ).fetchone()
        if row is None:
            return None
        return LongTermProfile(
            tenant_id=row["tenant_id"],
            user_id=row["user_id"],
            positive_preferences=tuple(json.loads(row["positive_preferences"])),
            negative_preferences=tuple(json.loads(row["negative_preferences"])),
            consent=ConsentState(
                personalization_enabled=bool(row["personalization_enabled"]),
                behavior_storage_enabled=bool(row["behavior_storage_enabled"]),
                updated_at=row["consent_updated_at"],
            ),
            version=int(row["version"]),
            updated_at=row["updated_at"],
        )

    async def put(self, profile: LongTermProfile) -> None:
        await asyncio.to_thread(self._put, profile)

    def _put(self, profile: LongTermProfile) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute(
                """
                INSERT INTO long_term_profiles VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(tenant_id, user_id) DO UPDATE SET
                    positive_preferences = excluded.positive_preferences,
                    negative_preferences = excluded.negative_preferences,
                    personalization_enabled = excluded.personalization_enabled,
                    behavior_storage_enabled = excluded.behavior_storage_enabled,
                    consent_updated_at = excluded.consent_updated_at,
                    version = excluded.version,
                    updated_at = excluded.updated_at
                """,
                self._parameters(profile),
            )

    async def delete(self, tenant_id: str, user_id: str) -> bool:
        return await asyncio.to_thread(self._delete, tenant_id, user_id)

    def _delete(self, tenant_id: str, user_id: str) -> bool:
        with closing(self._connect()) as connection, connection:
            cursor = connection.execute(
                "DELETE FROM long_term_profiles WHERE tenant_id = ? AND user_id = ?",
                (tenant_id, user_id),
            )
            return cursor.rowcount > 0
