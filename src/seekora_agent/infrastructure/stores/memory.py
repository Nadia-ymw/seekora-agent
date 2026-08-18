"""In-memory adapters for local development and automated tests."""

from __future__ import annotations

import asyncio

from ...application.receipt import RecommendationReceipt
from ...application.session import SessionMessage, SessionState


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

    async def get(self, tenant_id: str, session_id: str) -> SessionState | None:
        async with self._lock:
            return self._sessions.get((tenant_id, session_id))


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
