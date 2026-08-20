"""本地联调用预置账户，不承担生产认证或用户管理职责。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .profile import ConsentState, LongTermProfile


@dataclass(frozen=True)
class TestUserAccount:
    """仅描述测试身份及初始画像，不包含密码、Token 等认证凭据。"""

    tenant_id: str
    user_id: str
    display_name: str
    default_session_id: str
    initial_profile: LongTermProfile

    def as_dict(self, profile: LongTermProfile | None = None) -> dict[str, Any]:
        current = profile or self.initial_profile
        return {
            "tenant_id": self.tenant_id,
            "user_id": self.user_id,
            "display_name": self.display_name,
            "default_session_id": self.default_session_id,
            "profile": current.as_dict(),
            "authentication_enabled": False,
            "development_only": True,
        }


def build_default_test_account() -> TestUserAccount:
    """创建可直接覆盖画像、曝光、反馈和行为召回链路的默认测试账户。"""
    tenant_id = "demo"
    user_id = "seekora-demo-user"
    return TestUserAccount(
        tenant_id=tenant_id,
        user_id=user_id,
        display_name="Seekora 测试用户",
        default_session_id="seekora-demo-session",
        initial_profile=LongTermProfile(
            tenant_id=tenant_id,
            user_id=user_id,
            positive_preferences=("轻薄", "长续航"),
            negative_preferences=("厚重",),
            # 测试账户显式开启两个授权，普通新用户仍保持默认关闭。
            consent=ConsentState(
                personalization_enabled=True,
                behavior_storage_enabled=True,
            ),
            version=1,
        ),
    )
