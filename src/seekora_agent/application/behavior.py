"""行为反馈授权、幂等写入和个性化读取的应用服务。"""

from __future__ import annotations

from collections import defaultdict
from typing import Protocol

from ..domain.behavior import BehaviorEvent, BehaviorWriteResult
from ..domain.event_pipeline import BehaviorIngestionResult
from .event_pipeline import BehaviorEventPipeline, BehaviorEventQueue
from .exposure import ExposureService
from .profile import ProfileService


class BehaviorConsentRequired(ValueError):
    """用户未授权行为保存或个性化使用时拒绝对应操作。"""


class BehaviorEventConflict(ValueError):
    """同一幂等键被用于不同业务载荷。"""


class BehaviorStore(Protocol):
    async def put_if_absent(self, event: BehaviorEvent) -> BehaviorWriteResult: ...

    async def list_by_user(
        self, tenant_id: str, user_id: str, limit: int = 500
    ) -> tuple[BehaviorEvent, ...]: ...

    async def delete_by_user(self, tenant_id: str, user_id: str) -> int: ...


class BehaviorService:
    """确保行为数据的保存和使用分别经过相应 Consent 检查。"""

    ACTION_WEIGHTS = {
        "exposure": 0.0,
        "click": 1.0,
        "favorite": 3.0,
        "dismiss": -4.0,
        "conversion": 5.0,
    }

    def __init__(
        self,
        store: BehaviorStore,
        profiles: ProfileService,
        exposures: ExposureService,
        queue: BehaviorEventQueue,
    ) -> None:
        self.store = store
        self.profiles = profiles
        self.exposures = exposures
        self.pipeline = BehaviorEventPipeline(queue, store)

    async def record(
        self, event: BehaviorEvent, user_agent: str | None = None
    ) -> BehaviorIngestionResult:
        profile = await self.profiles.get(event.tenant_id, event.user_id)
        if not profile.consent.behavior_storage_enabled:
            raise BehaviorConsentRequired("behavior storage consent is required")
        normalized = await self.exposures.validate_and_normalize(event)
        return await self.pipeline.ingest(normalized, user_agent)

    async def replay(self, tenant_id: str, event_id: str) -> BehaviorIngestionResult:
        """按幂等键重放队列事件，Sink 已有数据时不会产生重复行为。"""
        return await self.pipeline.replay(tenant_id, event_id)

    async def item_scores(
        self, tenant_id: str, user_id: str, limit: int = 500
    ) -> dict[str, float]:
        profile = await self.profiles.get(tenant_id, user_id)
        # 行为可以被保存，不代表可以被用于个性化；读取时必须同时满足两个授权。
        if not (
            profile.consent.behavior_storage_enabled
            and profile.consent.personalization_enabled
        ):
            return {}
        events = await self.store.list_by_user(tenant_id, user_id, limit)
        scores: defaultdict[str, float] = defaultdict(float)
        for event in events:
            scores[event.item_id] += self.ACTION_WEIGHTS[event.action]
        return {item_id: score for item_id, score in scores.items() if score > 0}

    async def delete_user_data(self, tenant_id: str, user_id: str) -> int:
        """隐私删除不依赖当前 Consent，确保撤回授权后仍能清理历史行为。"""
        return await self.store.delete_by_user(tenant_id, user_id)

    async def delete_queued_data(self, tenant_id: str, user_id: str) -> int:
        """隐私删除还必须清理持久化队列中的原始事件载荷。"""
        return await self.pipeline.queue.delete_by_user(tenant_id, user_id)
