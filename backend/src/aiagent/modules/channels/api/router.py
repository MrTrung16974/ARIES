"""API kênh publish (ADR-0020): quản lý (cần đăng nhập) + công khai (link chia sẻ, API key)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Response, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from aiagent.api.deps import ContainerDep, get_principal
from aiagent.modules.channels.domain import ApiKey, ShareLink
from aiagent.modules.identity.domain import Principal
from aiagent.modules.runtime.domain import RunEvent, RunEventType
from aiagent.shared_kernel.errors import ForbiddenError, NotFoundError, UnauthorizedError

router = APIRouter(prefix="/api/v1", tags=["channels"])
public_router = APIRouter(prefix="/api/v1/public", tags=["public"])

AnyUser = Annotated[Principal, Depends(get_principal)]


# ---------------------------------------------------------------- quản lý
class ApiKeyOut(BaseModel):
    id: UUID
    agent_id: UUID
    name: str
    prefix: str
    rate_limit_per_min: int
    created_by: str
    created_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None


class ApiKeyCreatedOut(ApiKeyOut):
    key: str  # chỉ trả MỘT lần


class CreateApiKeyIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    rate_limit_per_min: int = Field(default=60, ge=1, le=600)


class ShareOut(BaseModel):
    id: UUID
    agent_id: UUID
    name: str
    prefix: str
    enabled: bool
    allowed_origins: list[str]
    rate_limit_per_min: int
    daily_message_limit: int
    created_by: str
    created_at: datetime
    updated_at: datetime
    last_used_at: datetime | None


class ShareCreatedOut(ShareOut):
    token: str  # chỉ trả MỘT lần — URL: /c/<token>


class CreateShareIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    allowed_origins: list[str] = Field(default_factory=list, max_length=20)
    rate_limit_per_min: int = Field(default=10, ge=1, le=120)
    daily_message_limit: int = Field(default=1000, ge=1, le=100_000)


class UpdateShareIn(BaseModel):
    name: str | None = Field(default=None, max_length=80)
    enabled: bool | None = None
    allowed_origins: list[str] | None = Field(default=None, max_length=20)
    rate_limit_per_min: int | None = Field(default=None, ge=1, le=120)
    daily_message_limit: int | None = Field(default=None, ge=1, le=100_000)


def _key_out(k: ApiKey) -> ApiKeyOut:
    return ApiKeyOut(**{f: getattr(k, f) for f in ApiKeyOut.model_fields})


def _share_out(s: ShareLink) -> ShareOut:
    return ShareOut(**{f: getattr(s, f) for f in ShareOut.model_fields})


@router.get("/agents/{agent_id}/api-keys", response_model=list[ApiKeyOut])
async def list_api_keys(agent_id: UUID, p: AnyUser, c: ContainerDep) -> list[ApiKeyOut]:
    return [_key_out(k) for k in await c.channels.list_api_keys(p, agent_id)]


@router.post(
    "/agents/{agent_id}/api-keys",
    response_model=ApiKeyCreatedOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_api_key(
    agent_id: UUID, body: CreateApiKeyIn, p: AnyUser, c: ContainerDep
) -> ApiKeyCreatedOut:
    key, token = await c.channels.create_api_key(
        p, agent_id, name=body.name, rate_limit_per_min=body.rate_limit_per_min
    )
    return ApiKeyCreatedOut(**_key_out(key).model_dump(), key=token)


@router.delete("/api-keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_api_key(key_id: UUID, p: AnyUser, c: ContainerDep) -> Response:
    await c.channels.revoke_api_key(p, key_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/agents/{agent_id}/share-links", response_model=list[ShareOut])
async def list_shares(agent_id: UUID, p: AnyUser, c: ContainerDep) -> list[ShareOut]:
    return [_share_out(s) for s in await c.channels.list_shares(p, agent_id)]


@router.post(
    "/agents/{agent_id}/share-links",
    response_model=ShareCreatedOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_share(
    agent_id: UUID, body: CreateShareIn, p: AnyUser, c: ContainerDep
) -> ShareCreatedOut:
    link, token = await c.channels.create_share(
        p,
        agent_id,
        name=body.name,
        allowed_origins=body.allowed_origins,
        rate_limit_per_min=body.rate_limit_per_min,
        daily_message_limit=body.daily_message_limit,
    )
    return ShareCreatedOut(**_share_out(link).model_dump(), token=token)


@router.patch("/share-links/{share_id}", response_model=ShareOut)
async def update_share(
    share_id: UUID, body: UpdateShareIn, p: AnyUser, c: ContainerDep
) -> ShareOut:
    kwargs: dict[str, Any] = body.model_dump(exclude_none=True)
    return _share_out(await c.channels.update_share(p, share_id, **kwargs))


@router.post("/share-links/{share_id}/rotate", response_model=ShareCreatedOut)
async def rotate_share(share_id: UUID, p: AnyUser, c: ContainerDep) -> ShareCreatedOut:
    link, token = await c.channels.rotate_share(p, share_id)
    return ShareCreatedOut(**_share_out(link).model_dump(), token=token)


@router.delete("/share-links/{share_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_share(share_id: UUID, p: AnyUser, c: ContainerDep) -> Response:
    await c.channels.delete_share(p, share_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------- công khai: link chia sẻ
def public_event(event: RunEvent) -> RunEvent | None:
    """Lọc sự kiện cho người ngoài: không lộ kết quả tool, thinking, chi phí, id nội bộ."""
    d = event.data
    t = event.type
    if t is RunEventType.TEXT:
        return event
    if t is RunEventType.RUN_STARTED:
        return RunEvent(t, {"thread_id": d.get("thread_id")})
    if t is RunEventType.TOOL_START:
        return RunEvent(t, {"id": d.get("id"), "name": d.get("name")})
    if t is RunEventType.TOOL_END:
        out = {"id": d.get("id"), "name": d.get("name"), "is_error": d.get("is_error")}
        if d.get("citations"):
            out["citations"] = d["citations"]
        return RunEvent(t, out)
    if t is RunEventType.ERROR:
        return RunEvent(t, {"message": d.get("message")})
    if t is RunEventType.DONE:
        return RunEvent(
            t,
            {
                "status": d.get("status"),
                "thread_id": d.get("thread_id"),
                "message_id": d.get("message_id"),
            },
        )
    return None


class ShareInfoOut(BaseModel):
    name: str
    description: str
    opening_statement: str
    starter_prompts: list[str]


class PublicChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    thread_id: UUID | None = None


class PublicFeedbackIn(BaseModel):
    thread_id: UUID
    message_id: int
    rating: int = Field(ge=-1, le=1)
    comment: str = Field(default="", max_length=1000)


async def _share_agent(c: Any, link: ShareLink, principal: Principal) -> Any:
    agent, version = await c.agents.resolve_for_run(principal, link.agent_id, draft=False)
    if version.spec.data_classification.value != "public":
        # Agent đã bị đổi mức dữ liệu sau khi tạo link → không phục vụ công khai nữa.
        raise NotFoundError("Link không tồn tại hoặc đã bị tắt.")
    return agent, version


@public_router.get("/share/{token}", response_model=ShareInfoOut)
async def share_info(
    token: str,
    c: ContainerDep,
    x_visitor_id: Annotated[str | None, Header()] = None,
) -> ShareInfoOut:
    link = await c.channels.resolve_share(token)
    viewer = c.channels.viewer_principal(link, x_visitor_id or "0" * 16)
    agent, version = await _share_agent(c, link, viewer)
    spec = version.spec
    return ShareInfoOut(
        name=agent.name,
        description=spec.description,
        opening_statement=spec.opening_statement,
        starter_prompts=spec.starter_prompts,
    )


@public_router.post(
    "/share/{token}/chat",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {}}}},
)
async def share_chat(
    token: str,
    body: PublicChatIn,
    c: ContainerDep,
    x_visitor_id: Annotated[str | None, Header()] = None,
    x_embed_origin: Annotated[str | None, Header()] = None,
) -> StreamingResponse:
    link = await c.channels.resolve_share(token)
    principal = await c.channels.share_principal(link, x_visitor_id, x_embed_origin)
    await _share_agent(c, link, principal)
    prepared = await c.runs.prepare(
        principal, link.agent_id, body.message, thread_id=body.thread_id, draft=False
    )

    async def events() -> AsyncIterator[str]:
        async for event in c.runs.stream(prepared):
            filtered = public_event(event)
            if filtered is not None:
                yield filtered.to_sse()

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@public_router.get("/share/{token}/threads/{thread_id}/messages")
async def share_messages(
    token: str,
    thread_id: UUID,
    c: ContainerDep,
    x_visitor_id: Annotated[str | None, Header()] = None,
) -> list[dict[str, Any]]:
    """Đọc lại hội thoại của CHÍNH visitor (chỉ văn bản)."""
    link = await c.channels.resolve_share(token)
    viewer = c.channels.viewer_principal(link, x_visitor_id or "")
    messages = await c.conversations.messages(viewer, thread_id)
    out = []
    for m in messages:
        text = "".join(str(b.get("text", "")) for b in m.content if b.get("type") == "text")
        if text:
            out.append({"id": m.id, "role": m.role, "text": text})
    return out


@public_router.post("/share/{token}/feedback", status_code=status.HTTP_204_NO_CONTENT)
async def share_feedback(
    token: str,
    body: PublicFeedbackIn,
    c: ContainerDep,
    x_visitor_id: Annotated[str | None, Header()] = None,
) -> Response:
    link = await c.channels.resolve_share(token)
    viewer = c.channels.viewer_principal(link, x_visitor_id or "")
    await c.conversations.give_feedback(
        viewer, body.thread_id, body.message_id, rating=body.rating, comment=body.comment
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------- công khai: API key
class ApiChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=20_000)
    conversation_id: UUID | None = None
    user: str | None = Field(default=None, max_length=80)  # định danh người dùng cuối
    stream: bool = False


class ApiChatOut(BaseModel):
    conversation_id: UUID
    message_id: int | None
    status: str
    answer: str
    citations: list[dict[str, Any]]
    error: str | None


@public_router.post("/v1/chat", response_model=ApiChatOut)
async def api_chat(
    body: ApiChatIn,
    c: ContainerDep,
    authorization: Annotated[str | None, Header()] = None,
) -> Any:
    """Gọi agent từ hệ thống khác: `Authorization: Bearer ak_…` (key gắn với một agent)."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise UnauthorizedError()
    key, principal = await c.channels.authenticate_api_key(
        authorization[7:].strip(), end_user=body.user
    )
    prepared = await c.runs.prepare(
        principal, key.agent_id, body.message, thread_id=body.conversation_id, draft=False
    )
    if body.stream:

        async def events() -> AsyncIterator[str]:
            async for event in c.runs.stream(prepared):
                filtered = public_event(event)
                if filtered is not None:
                    yield filtered.to_sse()

        return StreamingResponse(
            events(), media_type="text/event-stream", headers={"Cache-Control": "no-store"}
        )

    answer, citations, done, error = "", [], {}, None
    async for event in c.runs.stream(prepared):
        if event.type is RunEventType.TEXT:
            answer += str(event.data.get("text", ""))
        elif event.type is RunEventType.TOOL_START:
            answer = ""  # chỉ giữ câu trả lời sau lần gọi tool cuối
        elif event.type is RunEventType.TOOL_END and event.data.get("citations"):
            citations += list(event.data["citations"])
        elif event.type is RunEventType.ERROR:
            error = str(event.data.get("message"))
        elif event.type is RunEventType.DONE:
            done = event.data
    if done.get("status") == "awaiting_approval":  # không xảy ra với principal kênh
        raise ForbiddenError()
    return ApiChatOut(
        conversation_id=prepared.thread.id,
        message_id=done.get("message_id"),
        status=str(done.get("status", "failed")),
        answer=answer.strip(),
        citations=citations,
        error=error,
    )
