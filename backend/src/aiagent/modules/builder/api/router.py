from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, model_validator

from aiagent.api.deps import ContainerDep, require_roles
from aiagent.modules.builder.application import BuilderEvent
from aiagent.modules.identity.domain import Principal, Role

router = APIRouter(prefix="/api/v1", tags=["builder"])

Builder = Annotated[Principal, Depends(require_roles(Role.BUILDER, Role.ADMIN))]


class CreateSessionIn(BaseModel):
    agent_id: UUID | None = None  # sửa agent có sẵn; None = tạo mới


class MessageIn(BaseModel):
    message: str | None = Field(default=None, max_length=8000)
    answers: dict[str, str] | None = None  # trả lời câu hỏi làm rõ: {question_id: value}

    @model_validator(mode="after")
    def _one_of(self) -> MessageIn:
        if not (self.message and self.message.strip()) and not self.answers:
            raise ValueError("Cần message hoặc answers")
        return self


class ConfirmIn(BaseModel):
    action: str = Field(pattern="^publish$")
    override_eval: bool = False
    note: str = Field(default="", max_length=500)


class CommandIn(BaseModel):
    text: str = Field(min_length=1, max_length=8000)


async def _sse(events: AsyncIterator[BuilderEvent]) -> AsyncIterator[str]:
    async for event in events:
        yield event.to_sse()


@router.post("/builder/sessions", status_code=status.HTTP_201_CREATED)
async def create_session(body: CreateSessionIn, p: Builder, c: ContainerDep) -> dict[str, Any]:
    session = await c.architect.create_session(p, body.agent_id)
    return await c.architect.view(p, session)


@router.get("/builder/sessions")
async def list_sessions(p: Builder, c: ContainerDep) -> list[dict[str, Any]]:
    sessions = await c.architect.list_sessions(p)
    out = []
    for s in sessions:
        name = (s.draft_spec or {}).get("name") or (s.brief or {}).get("suggested_name")
        out.append(
            {
                "id": str(s.id),
                "status": s.status.value,
                "name": name or "(chưa đặt tên)",
                "agent_id": str(s.agent_id) if s.agent_id else None,
                "updated_at": s.updated_at,
            }
        )
    return out


@router.get("/builder/sessions/{session_id}")
async def get_session(session_id: UUID, p: Builder, c: ContainerDep) -> dict[str, Any]:
    session = await c.architect.owned(p, session_id)
    return {
        **(await c.architect.view(p, session)),
        "messages": await c.architect.history(p, session_id),
    }


@router.post(
    "/builder/sessions/{session_id}/messages",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {}}}},
)
async def post_message(
    session_id: UUID, body: MessageIn, p: Builder, c: ContainerDep
) -> StreamingResponse:
    """Gửi mô tả/yêu cầu sửa/trả lời câu hỏi; stream tiến trình Architect qua SSE."""
    await c.architect.owned(p, session_id)  # lỗi quyền trả HTTP trước khi mở stream
    events = c.architect.handle(p, session_id, message=body.message, answers=body.answers)
    return StreamingResponse(
        _sse(events),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@router.post("/builder/sessions/{session_id}/confirm")
async def confirm(session_id: UUID, body: ConfirmIn, p: Builder, c: ContainerDep) -> dict[str, Any]:
    return await c.architect.confirm_publish(
        p, session_id, override_eval=body.override_eval, note=body.note
    )


@router.post("/commands")
async def run_command(body: CommandIn, p: Builder, c: ContainerDep) -> dict[str, Any]:
    """Slash command (xác định, không gọi LLM). Văn bản không có `/` = `/agent new`."""
    return await c.commands.execute(p, body.text)
