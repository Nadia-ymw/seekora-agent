"""由显式授权控制的长期用户画像领域契约。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class ConsentState:
    """用户对个性化和行为保存的显式授权状态，默认全部关闭。"""

    personalization_enabled: bool = False
    behavior_storage_enabled: bool = False
    updated_at: str = field(default_factory=utc_now)

    def as_dict(self) -> dict[str, Any]:
        return {
            "personalization_enabled": self.personalization_enabled,
            "behavior_storage_enabled": self.behavior_storage_enabled,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True)
class LongTermProfile:
    """与 Session Intent 分离的长期显式偏好，不包含模型隐式推断。"""

    tenant_id: str
    user_id: str
    positive_preferences: tuple[str, ...] = ()
    negative_preferences: tuple[str, ...] = ()
    consent: ConsentState = field(default_factory=ConsentState)
    version: int = 0
    updated_at: str = field(default_factory=utc_now)

    def as_dict(self) -> dict[str, Any]:
        return {
            "tenant_id": self.tenant_id,
            "user_id": self.user_id,
            "positive_preferences": list(self.positive_preferences),
            "negative_preferences": list(self.negative_preferences),
            "consent": self.consent.as_dict(),
            "version": self.version,
            "updated_at": self.updated_at,
        }
