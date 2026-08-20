"""曝光清单登记、反馈校验和隐私删除应用服务。"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from typing import Protocol, Sequence
from uuid import uuid4

from ..domain.behavior import BehaviorEvent
from ..domain.exposure import ExposedItem, ExposureRecord
from .profile import ProfileService


class ExposureValidationError(ValueError):
    """反馈无法与服务端真实曝光清单建立可信关联。"""


class ExposureStore(Protocol):
    async def put(self, exposure: ExposureRecord) -> None: ...

    async def get(self, tenant_id: str, exposure_id: str) -> ExposureRecord | None: ...

    async def delete_by_user(self, tenant_id: str, user_id: str) -> int: ...


class ExposureService:
    """只为已授权用户登记曝光，并用服务端清单标准化反馈。"""

    def __init__(
        self,
        store: ExposureStore,
        profiles: ProfileService,
        clock_skew: timedelta = timedelta(minutes=5),
    ) -> None:
        self.store = store
        self.profiles = profiles
        self.clock_skew = clock_skew

    async def register(
        self,
        tenant_id: str,
        user_id: str | None,
        session_id: str,
        request_id: str,
        items: Sequence[dict],
        model_version: str,
    ) -> ExposureRecord | None:
        if user_id is None or not items:
            return None
        profile = await self.profiles.get(tenant_id, user_id)
        if not profile.consent.behavior_storage_enabled:
            return None
        exposed_items = tuple(
            ExposedItem(
                item_id=str(item["item_id"]),
                position=position,
                recall_sources=tuple(sorted(item.get("source_scores", {}).keys())),
                source_scores=tuple(
                    sorted(
                        (str(source), float(score))
                        for source, score in item.get("source_scores", {}).items()
                    )
                ),
            )
            for position, item in enumerate(items)
        )
        exposure = ExposureRecord(
            exposure_id=uuid4().hex,
            tenant_id=tenant_id,
            user_id=user_id,
            session_id=session_id,
            request_id=request_id,
            items=exposed_items,
            model_version=model_version,
        )
        await self.store.put(exposure)
        return exposure

    async def validate_and_normalize(self, event: BehaviorEvent) -> BehaviorEvent:
        exposure = await self.store.get(event.tenant_id, event.exposure_id)
        if exposure is None:
            raise ExposureValidationError("exposure not found")
        if (
            exposure.user_id != event.user_id
            or exposure.session_id != event.session_id
            or exposure.request_id != event.request_id
        ):
            raise ExposureValidationError("feedback identity does not match exposure")
        exposed_item = next(
            (item for item in exposure.items if item.item_id == event.item_id), None
        )
        if exposed_item is None:
            raise ExposureValidationError("item was not included in exposure")
        if event.position != exposed_item.position:
            raise ExposureValidationError("feedback position does not match exposure")
        occurred_at = datetime.fromisoformat(event.occurred_at.replace("Z", "+00:00"))
        created_at = datetime.fromisoformat(exposure.created_at.replace("Z", "+00:00"))
        if occurred_at < created_at - self.clock_skew:
            raise ExposureValidationError("feedback occurred before exposure")
        # 来源和模型版本以服务端曝光为准，不能信任客户端自报的归因字段。
        return replace(
            event,
            recall_sources=exposed_item.recall_sources,
            model_version=exposure.model_version,
        )

    async def delete_user_data(self, tenant_id: str, user_id: str) -> int:
        return await self.store.delete_by_user(tenant_id, user_id)
