"""In-memory adapters for local development and automated tests."""

from __future__ import annotations

import asyncio

from ...application.receipt import RecommendationReceipt
from ...application.session import SessionIntentSnapshot, SessionMessage, SessionState
from ...domain.profile import LongTermProfile


class InMemorySessionStore:
    def __init__(self) -> None:
        self._sessions: dict[tuple[str, str], SessionState] = {}
        self._lock = asyncio.Lock()

    async def get_or_create(
        self, session_id: str, tenant_id: str, user_id: str | None
    ) -> SessionState:
        key = (tenant_id, session_id)
        async with self._lock:
            state = self._sessions.get(key)
            if state is None:
                state = SessionState(session_id, tenant_id, user_id)
                self._sessions[key] = state
            return state

    async def append(self, state: SessionState, message: SessionMessage) -> None:
        async with self._lock:
            state.messages.append(message)
            state.version += 1

    async def set_intent(
        self, state: SessionState, snapshot: SessionIntentSnapshot
    ) -> None:
        async with self._lock:
            # Session Intent 仅覆盖当前任务状态，不会自动同步到长期 Profile。
            state.current_intent = snapshot
            state.version += 1

    async def get(self, tenant_id: str, session_id: str) -> SessionState | None:
        async with self._lock:
            return self._sessions.get((tenant_id, session_id))


class InMemoryProfileStore:
    """用于本地开发的画像存储，按 tenant_id 与 user_id 联合隔离。"""

    def __init__(self) -> None:
        self._profiles: dict[tuple[str, str], LongTermProfile] = {}
        self._lock = asyncio.Lock()

    async def get(self, tenant_id: str, user_id: str) -> LongTermProfile | None:
        async with self._lock:
            return self._profiles.get((tenant_id, user_id))

    async def put(self, profile: LongTermProfile) -> None:
        async with self._lock:
            self._profiles[(profile.tenant_id, profile.user_id)] = profile

    async def delete(self, tenant_id: str, user_id: str) -> bool:
        async with self._lock:
            # 显式删除接口保证用户可以撤回并清理已经保存的长期画像。
            return self._profiles.pop((tenant_id, user_id), None) is not None


class InMemoryCancellationRegistry:
    def __init__(self) -> None:
        self._cancelled: set[str] = set()
        self._lock = asyncio.Lock()

    async def cancel(self, request_id: str) -> None:
        async with self._lock:
            self._cancelled.add(request_id)

    async def is_cancelled(self, request_id: str) -> bool:
        async with self._lock:
            return request_id in self._cancelled

    async def clear(self, request_id: str) -> None:
        async with self._lock:
            self._cancelled.discard(request_id)


class InMemoryReceiptStore:
    def __init__(self) -> None:
        self._receipts: dict[str, RecommendationReceipt] = {}
        self._lock = asyncio.Lock()

    async def put(self, receipt: RecommendationReceipt) -> None:
        async with self._lock:
            self._receipts[receipt.request_id] = receipt

    async def get(self, request_id: str) -> RecommendationReceipt | None:
        async with self._lock:
            return self._receipts.get(request_id)
