"""客户端请求幂等、并发占用和事件回放契约。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Literal, Protocol

from .contracts import AgentEvent, AgentQuery


ReservationAction = Literal["execute", "replay", "in_progress", "conflict"]


@dataclass(frozen=True)
class RequestReservation:
    action: ReservationAction
    request_id: str
    events: tuple[AgentEvent, ...] = ()


class RequestReplayStore(Protocol):
    async def reserve(
        self,
        tenant_id: str,
        client_request_id: str,
        fingerprint: str,
        request_id: str,
    ) -> RequestReservation: ...

    async def complete(
        self,
        tenant_id: str,
        client_request_id: str,
        request_id: str,
        events: tuple[AgentEvent, ...],
    ) -> None: ...

    async def release(
        self,
        tenant_id: str,
        client_request_id: str,
        request_id: str,
    ) -> None: ...


def request_fingerprint(query: AgentQuery) -> str:
    """对影响执行结果和安全边界的字段生成稳定指纹。"""
    payload = {
        "query": query.query,
        "tenant_id": query.tenant_id,
        "session_id": query.session_id,
        "user_id": query.user_id,
        "allowed_permission_tags": sorted(query.allowed_permission_tags),
        "top_k": query.top_k,
    }
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
