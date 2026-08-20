"""持久化队列、迟到分类、机器人过滤与事件重放应用服务。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Callable, Protocol

from ..domain.behavior import BehaviorEvent, BehaviorWriteResult
from ..domain.event_pipeline import BehaviorIngestionResult, QueuedBehaviorEvent


class BotTrafficRejected(ValueError):
    """服务端观测到明显机器人客户端时拒绝行为归因。"""


class EventTimestampRejected(ValueError):
    """事件超出可接受迟到窗口或时间明显来自未来。"""


class QueueEventConflict(ValueError):
    """持久化队列中同一幂等键对应不同业务载荷。"""


class BehaviorEventSink(Protocol):
    async def put_if_absent(self, event: BehaviorEvent) -> BehaviorWriteResult: ...


class BehaviorEventQueue(Protocol):
    async def enqueue(
        self, event: BehaviorEvent, late: bool
    ) -> tuple[QueuedBehaviorEvent, bool]: ...

    async def get(self, tenant_id: str, event_id: str) -> QueuedBehaviorEvent | None: ...

    async def mark_processed(self, tenant_id: str, event_id: str) -> None: ...

    async def mark_failed(self, tenant_id: str, event_id: str, error: str) -> None: ...

    async def requeue(self, tenant_id: str, event_id: str) -> QueuedBehaviorEvent: ...

    async def delete_by_user(self, tenant_id: str, user_id: str) -> int: ...


class UserAgentBotFilter:
    """保守匹配常见自动化标识，避免把爬虫行为作为个性化正反馈。"""

    TOKENS = (
        "bot",
        "spider",
        "crawler",
        "headless",
        "selenium",
        "python-requests",
        "scrapy",
    )

    def is_bot(self, user_agent: str | None) -> bool:
        normalized = (user_agent or "").lower()
        return any(token in normalized for token in self.TOKENS)


class BehaviorEventPipeline:
    """先写队列再投递 Sink，并保留可重放状态。"""

    def __init__(
        self,
        queue: BehaviorEventQueue,
        sink: BehaviorEventSink,
        watermark: timedelta = timedelta(hours=24),
        max_age: timedelta = timedelta(days=30),
        future_tolerance: timedelta = timedelta(minutes=5),
        now: Callable[[], datetime] | None = None,
        bot_filter: UserAgentBotFilter | None = None,
    ) -> None:
        self.queue = queue
        self.sink = sink
        self.watermark = watermark
        self.max_age = max_age
        self.future_tolerance = future_tolerance
        self.now = now or (lambda: datetime.now(UTC))
        self.bot_filter = bot_filter or UserAgentBotFilter()

    async def ingest(
        self, event: BehaviorEvent, user_agent: str | None = None
    ) -> BehaviorIngestionResult:
        if self.bot_filter.is_bot(user_agent):
            raise BotTrafficRejected("bot traffic is not accepted as user feedback")
        late = self._classify_lateness(event)
        queued, queue_duplicate = await self.queue.enqueue(event, late)
        try:
            written = await self.sink.put_if_absent(queued.event)
            await self.queue.mark_processed(event.tenant_id, event.event_id)
        except Exception as exc:
            # 队列保留失败状态和错误摘要，后续可按 event_id 安全重放。
            await self.queue.mark_failed(event.tenant_id, event.event_id, type(exc).__name__)
            raise
        return BehaviorIngestionResult(
            event=written.event,
            duplicate=queue_duplicate or written.duplicate,
            late=queued.late,
            queue_status="processed",
        )

    async def replay(self, tenant_id: str, event_id: str) -> BehaviorIngestionResult:
        queued = await self.queue.requeue(tenant_id, event_id)
        try:
            written = await self.sink.put_if_absent(queued.event)
            await self.queue.mark_processed(tenant_id, event_id)
        except Exception as exc:
            await self.queue.mark_failed(tenant_id, event_id, type(exc).__name__)
            raise
        return BehaviorIngestionResult(
            event=written.event,
            duplicate=written.duplicate,
            late=queued.late,
            queue_status="processed",
            replayed=True,
        )

    def _classify_lateness(self, event: BehaviorEvent) -> bool:
        occurred_at = datetime.fromisoformat(event.occurred_at.replace("Z", "+00:00"))
        age = self.now() - occurred_at
        if age < -self.future_tolerance:
            raise EventTimestampRejected("event timestamp is too far in the future")
        if age > self.max_age:
            raise EventTimestampRejected("event exceeds maximum accepted age")
        return age > self.watermark
