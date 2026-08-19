from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Annotated
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Path as ApiPath, Query
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from ...application.contracts import AgentEvent, AgentQuery
from ...application.profile import ConsentRequired, ProfileService
from ...application.runtime import AgentRuntime


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


ProfileUserId = Annotated[str, ApiPath(min_length=1, max_length=128)]
ProfileTenantId = Annotated[str, Query(min_length=1, max_length=128)]


def encode_sse(event: AgentEvent) -> str:
    payload = json.dumps(event.as_dict(), ensure_ascii=False, separators=(",", ":"))
    return f"event: {event.event}\ndata: {payload}\n\n"


def create_app(runtime: AgentRuntime) -> FastAPI:
    app = FastAPI(title="Seekora Agent", version="0.10.0")
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
    ) -> dict[str, bool]:
        deleted = await profile_service().delete(tenant_id, user_id)
        return {"deleted": deleted}

    return app
