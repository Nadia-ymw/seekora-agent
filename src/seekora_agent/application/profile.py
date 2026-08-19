"""感知授权状态的长期画像应用服务与持久化端口。"""

from __future__ import annotations

from typing import Protocol, Sequence

from ..domain.profile import ConsentState, LongTermProfile, utc_now


class ConsentRequired(ValueError):
    """未授权个性化时拒绝写入或使用长期偏好。"""


class ProfileStore(Protocol):
    async def get(self, tenant_id: str, user_id: str) -> LongTermProfile | None: ...

    async def put(self, profile: LongTermProfile) -> None: ...

    async def delete(self, tenant_id: str, user_id: str) -> bool: ...


class ProfileService:
    """只管理用户显式提交的长期偏好，不从会话文本自动推断画像。"""

    def __init__(self, store: ProfileStore, max_preferences: int = 50) -> None:
        self.store = store
        self.max_preferences = max_preferences

    async def get(self, tenant_id: str, user_id: str) -> LongTermProfile:
        self._validate_identity(tenant_id, user_id)
        profile = await self.store.get(tenant_id, user_id)
        # 读取不存在的画像时返回未授权空视图，但不产生隐式持久化写入。
        return profile or LongTermProfile(tenant_id=tenant_id, user_id=user_id)

    async def update_consent(
        self,
        tenant_id: str,
        user_id: str,
        personalization_enabled: bool,
        behavior_storage_enabled: bool,
    ) -> LongTermProfile:
        current = await self.get(tenant_id, user_id)
        updated = LongTermProfile(
            tenant_id=tenant_id,
            user_id=user_id,
            positive_preferences=current.positive_preferences,
            negative_preferences=current.negative_preferences,
            consent=ConsentState(
                personalization_enabled=personalization_enabled,
                behavior_storage_enabled=behavior_storage_enabled,
            ),
            version=current.version + 1,
        )
        await self.store.put(updated)
        return updated

    async def replace_preferences(
        self,
        tenant_id: str,
        user_id: str,
        positive_preferences: Sequence[str],
        negative_preferences: Sequence[str],
    ) -> LongTermProfile:
        current = await self.get(tenant_id, user_id)
        if not current.consent.personalization_enabled:
            raise ConsentRequired("personalization consent is required")
        positive = self._normalize_preferences(positive_preferences)
        negative = self._normalize_preferences(negative_preferences)
        updated = LongTermProfile(
            tenant_id=tenant_id,
            user_id=user_id,
            positive_preferences=positive,
            negative_preferences=negative,
            consent=current.consent,
            version=current.version + 1,
            updated_at=utc_now(),
        )
        await self.store.put(updated)
        return updated

    async def ranking_snapshot(
        self, tenant_id: str, user_id: str
    ) -> LongTermProfile | None:
        profile = await self.get(tenant_id, user_id)
        # Consent 关闭后保留用户可查询/删除的数据，但排序链路不得读取这些偏好。
        return profile if profile.consent.personalization_enabled else None

    async def delete(self, tenant_id: str, user_id: str) -> bool:
        self._validate_identity(tenant_id, user_id)
        return await self.store.delete(tenant_id, user_id)

    def _normalize_preferences(self, values: Sequence[str]) -> tuple[str, ...]:
        normalized = tuple(dict.fromkeys(value.strip() for value in values if value.strip()))
        if len(normalized) > self.max_preferences:
            raise ValueError(f"preferences exceed maximum count: {self.max_preferences}")
        if any(len(value) > 128 for value in normalized):
            raise ValueError("preference must not exceed 128 characters")
        return normalized

    @staticmethod
    def _validate_identity(tenant_id: str, user_id: str) -> None:
        if not tenant_id.strip() or not user_id.strip():
            raise ValueError("tenant_id and user_id must not be empty")
