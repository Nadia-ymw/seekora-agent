from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from datetime import datetime
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import FastAPI, Header, HTTPException, Path as ApiPath, Query, Response
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from ...application.contracts import AgentEvent, AgentQuery
from ...application.behavior import (
    BehaviorConsentRequired,
    BehaviorEventConflict,
    BehaviorService,
)
from ...application.exposure import ExposureValidationError
from ...application.event_pipeline import (
    BotTrafficRejected,
    EventTimestampRejected,
    QueueEventConflict,
)
from ...application.profile import ConsentRequired, ProfileService
from ...application.runtime import AgentRuntime
from ...domain.behavior import BehaviorEvent


class QueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=4_000)
    tenant_id: str = Field(min_length=1, max_length=128)
    session_id: str | None = Field(default=None, max_length=128)
    user_id: str | None = Field(default=None, max_length=128)
    client_request_id: str | None = Field(default=None, max_length=128)
    top_k: int = Field(default=10, ge=1, le=50)


class ConsentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    personalization_enabled: bool
    behavior_storage_enabled: bool


class ProfilePreferencesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    positive_preferences: list[str] = Field(default_factory=list, max_length=50)
    negative_preferences: list[str] = Field(default_factory=list, max_length=50)


class BehaviorEventRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(min_length=1, max_length=128)
    tenant_id: str = Field(min_length=1, max_length=128)
    user_id: str = Field(min_length=1, max_length=128)
    session_id: str = Field(min_length=1, max_length=128)
    request_id: str = Field(min_length=1, max_length=128)
    exposure_id: str = Field(min_length=1, max_length=128)
    item_id: str = Field(min_length=1, max_length=128)
    action: Literal["exposure", "click", "favorite", "dismiss", "conversion"]
    occurred_at: datetime
    position: int | None = Field(default=None, ge=0)
    recall_sources: list[str] = Field(default_factory=list, max_length=20)
    model_version: str = Field(default="unknown", min_length=1, max_length=128)


ProfileUserId = Annotated[str, ApiPath(min_length=1, max_length=128)]
ProfileTenantId = Annotated[str, Query(min_length=1, max_length=128)]


def encode_sse(event: AgentEvent) -> str:
    payload = json.dumps(event.as_dict(), ensure_ascii=False, separators=(",", ":"))
    return f"event: {event.event}\ndata: {payload}\n\n"


def create_app(runtime: AgentRuntime) -> FastAPI:
    app = FastAPI(title="Seekora Agent", version="0.16.0")
    app.state.runtime = runtime
    static_dir = Path(__file__).with_name("static")
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.get("/", include_in_schema=False)
    async def chat_page() -> FileResponse:
        return FileResponse(static_dir / "index.html")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/agent/config")
    async def public_config() -> dict[str, str]:
        resolver = runtime.workflow.intent_resolver
        resolver_version = getattr(
            resolver,
            "resolver_version",
            getattr(resolver, "version", "unknown"),
        )
        return {
            "framework": "langchain/langgraph",
            "resolver_version": str(resolver_version),
        }

    @app.get("/agent/dev/account")
    async def development_account() -> dict:
        account = runtime.test_account
        if account is None:
            raise HTTPException(status_code=404, detail="development account unavailable")
        # 接口只暴露非敏感测试身份；它不是登录接口，也不会签发任何凭据。
        current_profile = (
            await runtime.profiles.get(account.tenant_id, account.user_id)
            if runtime.profiles is not None
            else account.initial_profile
        )
        return account.as_dict(current_profile)

    @app.post("/agent/query")
    async def agent_query(body: QueryRequest) -> StreamingResponse:
        query = AgentQuery(
            query=body.query,
            tenant_id=body.tenant_id,
            session_id=body.session_id or uuid4().hex,
            user_id=body.user_id,
            client_request_id=body.client_request_id,
            # The public demo API never trusts permission tags supplied by a caller.
            # A future authenticated gateway will inject verified ACL claims here.
            allowed_permission_tags=("public",),
            top_k=body.top_k,
        )

        async def stream() -> AsyncIterator[str]:
            async for event in runtime.run(query):
                yield encode_sse(event)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/agent/requests/{request_id}/cancel")
    async def cancel(request_id: str) -> dict[str, str]:
        await runtime.cancellations.cancel(request_id)
        return {"request_id": request_id, "status": "cancellation_requested"}

    @app.get("/agent/receipts/{request_id}")
    async def get_receipt(request_id: str) -> dict:
        receipt = await runtime.receipts.get(request_id)
        if receipt is None:
            raise HTTPException(status_code=404, detail="receipt not found")
        return receipt.as_dict()

    def profile_service() -> ProfileService:
        if runtime.profiles is None:
            raise HTTPException(status_code=503, detail="profile service unavailable")
        return runtime.profiles

    def behavior_service() -> BehaviorService:
        if runtime.behaviors is None:
            raise HTTPException(status_code=503, detail="behavior service unavailable")
        return runtime.behaviors

    @app.post("/agent/feedback")
    async def record_feedback(
        body: BehaviorEventRequest,
        response: Response,
        user_agent: Annotated[str | None, Header(alias="User-Agent")] = None,
    ) -> dict:
        # 本地演示由请求携带身份；生产环境必须覆盖为认证网关确认的 tenant/user。
        event = BehaviorEvent(
            event_id=body.event_id,
            tenant_id=body.tenant_id,
            user_id=body.user_id,
            session_id=body.session_id,
            request_id=body.request_id,
            exposure_id=body.exposure_id,
            item_id=body.item_id,
            action=body.action,
            occurred_at=body.occurred_at.isoformat(),
            position=body.position,
            recall_sources=tuple(body.recall_sources),
            model_version=body.model_version,
        )
        try:
            result = await behavior_service().record(event, user_agent)
        except BehaviorConsentRequired as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except BehaviorEventConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ExposureValidationError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except QueueEventConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except BotTrafficRejected as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except EventTimestampRejected as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        response.status_code = 200 if result.duplicate else 201
        return result.as_dict()

    @app.get("/agent/profiles/{user_id}")
    async def get_profile(user_id: ProfileUserId, tenant_id: ProfileTenantId) -> dict:
        # 本地演示接口接收身份参数；生产环境必须由认证网关注入可信 tenant/user。
        profile = await profile_service().get(tenant_id, user_id)
        return profile.as_dict()

    @app.put("/agent/profiles/{user_id}/consent")
    async def update_profile_consent(
        user_id: ProfileUserId, body: ConsentRequest, tenant_id: ProfileTenantId
    ) -> dict:
        profile = await profile_service().update_consent(
            tenant_id,
            user_id,
            body.personalization_enabled,
            body.behavior_storage_enabled,
        )
        return profile.as_dict()

    @app.put("/agent/profiles/{user_id}/preferences")
    async def update_profile_preferences(
        user_id: ProfileUserId,
        body: ProfilePreferencesRequest,
        tenant_id: ProfileTenantId,
    ) -> dict:
        try:
            profile = await profile_service().replace_preferences(
                tenant_id,
                user_id,
                body.positive_preferences,
                body.negative_preferences,
            )
        except ConsentRequired as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return profile.as_dict()

    @app.delete("/agent/profiles/{user_id}")
    async def delete_profile(
        user_id: ProfileUserId, tenant_id: ProfileTenantId
    ) -> dict[str, bool | int]:
        behavior_events_deleted = 0
        exposures_deleted = 0
        queued_events_deleted = 0
        if runtime.behaviors is not None:
            behavior_events_deleted = await runtime.behaviors.delete_user_data(
                tenant_id, user_id
            )
            queued_events_deleted = await runtime.behaviors.delete_queued_data(
                tenant_id, user_id
            )
        if runtime.exposures is not None:
            exposures_deleted = await runtime.exposures.delete_user_data(
                tenant_id, user_id
            )
        deleted = await profile_service().delete(tenant_id, user_id)
        return {
            "deleted": deleted,
            "behavior_events_deleted": behavior_events_deleted,
            "exposures_deleted": exposures_deleted,
            "queued_events_deleted": queued_events_deleted,
        }

    return app
