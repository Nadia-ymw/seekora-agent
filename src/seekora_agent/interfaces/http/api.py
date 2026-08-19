from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from ...application.contracts import AgentEvent, AgentQuery
from ...application.runtime import AgentRuntime


class QueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=4_000)
    tenant_id: str = Field(min_length=1, max_length=128)
    session_id: str | None = Field(default=None, max_length=128)
    user_id: str | None = Field(default=None, max_length=128)
    client_request_id: str | None = Field(default=None, max_length=128)
    top_k: int = Field(default=10, ge=1, le=50)


def encode_sse(event: AgentEvent) -> str:
    payload = json.dumps(event.as_dict(), ensure_ascii=False, separators=(",", ":"))
    return f"event: {event.event}\ndata: {payload}\n\n"


def create_app(runtime: AgentRuntime) -> FastAPI:
    app = FastAPI(title="Seekora Agent", version="0.8.0")
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

    return app
