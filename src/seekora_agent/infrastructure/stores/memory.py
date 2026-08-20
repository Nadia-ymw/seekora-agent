"""In-memory adapters for local development and automated tests."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from collections.abc import Iterable

from ...application.behavior import BehaviorEventConflict
from ...application.event_pipeline import QueueEventConflict
from ...application.receipt import RecommendationReceipt
from ...application.session import SessionIntentSnapshot, SessionMessage, SessionState
from ...domain.behavior import BehaviorEvent, BehaviorWriteResult
from ...domain.exposure import ExposureRecord
from ...domain.event_pipeline import QueuedBehaviorEvent
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

    def __init__(self, initial_profiles: Iterable[LongTermProfile] = ()) -> None:
        # 只允许通过构造参数注入明确的开发/测试数据，不隐式创建真实用户画像。
        self._profiles: dict[tuple[str, str], LongTermProfile] = {
            (profile.tenant_id, profile.user_id): profile
            for profile in initial_profiles
        }
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


class InMemoryBehaviorStore:
    """用于本地测试的幂等行为事件存储，不在进程之间共享数据。"""

    def __init__(self) -> None:
        self._events: dict[tuple[str, str], BehaviorEvent] = {}
        self._lock = asyncio.Lock()

    async def put_if_absent(self, event: BehaviorEvent) -> BehaviorWriteResult:
        key = (event.tenant_id, event.event_id)
        async with self._lock:
            existing = self._events.get(key)
            if existing is None:
                self._events[key] = event
                return BehaviorWriteResult(event=event, duplicate=False)
            if existing.idempotency_payload() != event.idempotency_payload():
                # 相同 event_id 的不同载荷不能静默覆盖，否则会破坏归因审计。
                raise BehaviorEventConflict("event_id already exists with different payload")
            return BehaviorWriteResult(event=existing, duplicate=True)

    async def list_by_user(
        self, tenant_id: str, user_id: str, limit: int = 500
    ) -> tuple[BehaviorEvent, ...]:
        async with self._lock:
            events = [
                event
                for event in self._events.values()
                if event.tenant_id == tenant_id and event.user_id == user_id
            ]
            events.sort(key=lambda event: (event.occurred_at, event.event_id), reverse=True)
            return tuple(events[:limit])

    async def delete_by_user(self, tenant_id: str, user_id: str) -> int:
        async with self._lock:
            keys = [
                key
                for key, event in self._events.items()
                if event.tenant_id == tenant_id and event.user_id == user_id
            ]
            # 先收集精确键再删除，避免遍历字典时改变其大小。
            for key in keys:
                del self._events[key]
            return len(keys)


class InMemoryExposureStore:
    """本地曝光清单存储，按租户和 exposure_id 隔离。"""

    def __init__(self) -> None:
        self._exposures: dict[tuple[str, str], ExposureRecord] = {}
        self._lock = asyncio.Lock()

    async def put(self, exposure: ExposureRecord) -> None:
        async with self._lock:
            key = (exposure.tenant_id, exposure.exposure_id)
            if key in self._exposures:
                raise ValueError("exposure_id already exists")
            self._exposures[key] = exposure

    async def get(self, tenant_id: str, exposure_id: str) -> ExposureRecord | None:
        async with self._lock:
            return self._exposures.get((tenant_id, exposure_id))

    async def delete_by_user(self, tenant_id: str, user_id: str) -> int:
        async with self._lock:
            keys = [
                key
                for key, exposure in self._exposures.items()
                if exposure.tenant_id == tenant_id and exposure.user_id == user_id
            ]
            # 用户隐私删除同时清理可用于行为归因的曝光清单。
            for key in keys:
                del self._exposures[key]
            return len(keys)


class InMemoryBehaviorEventQueue:
    """测试用事件队列，模拟持久化队列的状态迁移和重放语义。"""

    def __init__(self) -> None:
        self._entries: dict[tuple[str, str], QueuedBehaviorEvent] = {}
        self._lock = asyncio.Lock()

    async def enqueue(
        self, event: BehaviorEvent, late: bool
    ) -> tuple[QueuedBehaviorEvent, bool]:
        key = (event.tenant_id, event.event_id)
        async with self._lock:
            existing = self._entries.get(key)
            if existing is None:
                queued = QueuedBehaviorEvent(event=event, late=late)
                self._entries[key] = queued
                return queued, False
            if existing.event.idempotency_payload() != event.idempotency_payload():
                raise QueueEventConflict("event_id already queued with different payload")
            return existing, True

    async def get(
        self, tenant_id: str, event_id: str
    ) -> QueuedBehaviorEvent | None:
        async with self._lock:
            return self._entries.get((tenant_id, event_id))

    async def mark_processed(self, tenant_id: str, event_id: str) -> None:
        async with self._lock:
            key = (tenant_id, event_id)
            entry = self._entries[key]
            self._entries[key] = replace(
                entry, status="processed", attempts=entry.attempts + 1, last_error=None
            )

    async def mark_failed(self, tenant_id: str, event_id: str, error: str) -> None:
        async with self._lock:
            key = (tenant_id, event_id)
            entry = self._entries[key]
            self._entries[key] = replace(
                entry, status="failed", attempts=entry.attempts + 1, last_error=error
            )

    async def requeue(self, tenant_id: str, event_id: str) -> QueuedBehaviorEvent:
        async with self._lock:
            key = (tenant_id, event_id)
            entry = self._entries.get(key)
            if entry is None:
                raise KeyError("queued event not found")
            queued = replace(entry, status="pending", last_error=None)
            self._entries[key] = queued
            return queued

    async def delete_by_user(self, tenant_id: str, user_id: str) -> int:
        async with self._lock:
            keys = [
                key
                for key, entry in self._entries.items()
                if entry.event.tenant_id == tenant_id and entry.event.user_id == user_id
            ]
            for key in keys:
                del self._entries[key]
            return len(keys)


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
