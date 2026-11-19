"""Template ReAct bằng LangGraph: model ↔ tool cho đến khi model trả lời xong (ADR-0014).

Graph không chạm DB: nó phát `RunEvent` qua stream writer, use case lưu và chuyển tiếp.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import replace
from typing import Any, TypedDict

from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph

from aiagent.modules.runtime.domain import (
    ChatMessage,
    LoopInput,
    RunEvent,
    RunEventType,
    TextDelta,
    ThinkingDelta,
    ToolCallStarted,
    TurnCompleted,
    executable_tool_uses,
)
from aiagent.shared_kernel.usage import Usage

# Trạng thái nội bộ của vòng lặp
_CONTINUE_TOOLS = "tools"
_CONTINUE_MODEL = "model"  # pause_turn: gọi lại model


class _State(TypedDict):
    messages: list[ChatMessage]
    iterations: int
    usage: Usage
    model: str
    next: str  # tools | model | end
    status: str  # completed | failed | refused
    error: str | None


def _emit(event: RunEvent) -> None:
    get_stream_writer()(event)


class LangGraphReactLoop:
    """Hiện thực `AgentLoopPort`."""

    def run(self, loop: LoopInput) -> AsyncIterator[RunEvent]:
        return self._run(loop)

    async def _run(self, loop: LoopInput) -> AsyncIterator[RunEvent]:
        graph = self._build(loop)
        initial: _State = {
            "messages": list(loop.request.messages),
            "iterations": 0,
            "usage": Usage(),
            "model": loop.request.model,
            "next": _CONTINUE_MODEL,
            "status": "failed",
            "error": None,
        }
        final: dict[str, Any] = dict(initial)
        async for mode, chunk in graph.astream(initial, stream_mode=["custom", "values"]):
            if mode == "custom" and isinstance(chunk, RunEvent):
                yield chunk
            elif mode == "values" and isinstance(chunk, dict):
                final = chunk
        yield RunEvent(
            RunEventType.DONE,
            {
                "status": final["status"],
                "error": final["error"],
                "usage": final["usage"],
                "model": final["model"],
            },
        )

    def _build(self, loop: LoopInput) -> Any:
        async def call_model(state: _State) -> dict[str, Any]:
            request = replace(loop.request, messages=state["messages"])
            turn = None
            async for ev in loop.model.stream(request):
                if isinstance(ev, TextDelta):
                    _emit(RunEvent(RunEventType.TEXT, {"text": ev.text}))
                elif isinstance(ev, ThinkingDelta):
                    _emit(RunEvent(RunEventType.THINKING, {"text": ev.text}))
                elif isinstance(ev, ToolCallStarted):
                    _emit(RunEvent(RunEventType.TOOL_START, {"id": ev.id, "name": ev.name}))
                elif isinstance(ev, TurnCompleted):
                    turn = ev.turn
            if turn is None:
                return {"next": "end", "status": "failed", "error": "Model không trả về kết quả."}

            usage = state["usage"] + turn.usage
            update: dict[str, Any] = {
                "usage": usage,
                "model": turn.model or state["model"],
                "iterations": state["iterations"] + 1,
            }
            _emit(RunEvent(RunEventType.USAGE, {**usage.as_dict(), "partial": True}))

            if turn.stop_reason == "refusal":
                # Phần đã stream (nếu có) bị bỏ, không lưu vào lịch sử.
                return {
                    **update,
                    "next": "end",
                    "status": "refused",
                    "error": "Mô hình từ chối yêu cầu này.",
                }

            assistant: ChatMessage = {"role": "assistant", "content": turn.content}
            update["messages"] = [*state["messages"], assistant]
            if turn.content:
                _emit(RunEvent(RunEventType.MESSAGE, assistant))

            tool_uses = executable_tool_uses(turn.content)
            if turn.stop_reason == "max_tokens":
                if tool_uses:
                    return {
                        **update,
                        "next": "end",
                        "status": "failed",
                        "error": "Câu trả lời bị cắt khi đang gọi tool (max_tokens).",
                    }
                return {
                    **update,
                    "next": "end",
                    "status": "completed",
                    "error": "Câu trả lời bị cắt do đạt giới hạn max_tokens.",
                }
            if usage.total > loop.max_tokens_per_run:
                return {
                    **update,
                    "next": "end",
                    "status": "failed",
                    "error": "Vượt giới hạn token của một lần chạy.",
                }
            if turn.stop_reason == "pause_turn":
                return {**update, "next": _CONTINUE_MODEL}
            if tool_uses:
                if update["iterations"] >= loop.max_iterations:
                    return {
                        **update,
                        "next": "end",
                        "status": "failed",
                        "error": f"Vượt số vòng tool tối đa ({loop.max_iterations}).",
                    }
                return {**update, "next": _CONTINUE_TOOLS}
            return {**update, "next": "end", "status": "completed", "error": None}

        async def run_tools(state: _State) -> dict[str, Any]:
            last = state["messages"][-1]
            tool_uses = executable_tool_uses(last["content"])
            outcomes = await asyncio.gather(*(loop.tools.execute(t) for t in tool_uses))
            results = []
            for tool_use, (result, info) in zip(tool_uses, outcomes, strict=True):
                results.append(result)
                _emit(RunEvent(RunEventType.TOOL_END, {"id": tool_use.get("id"), **info}))
            # Mọi tool_result của một lượt nằm trong MỘT tin user.
            message: ChatMessage = {"role": "user", "content": results}
            _emit(RunEvent(RunEventType.MESSAGE, message))
            return {"messages": [*state["messages"], message], "next": _CONTINUE_MODEL}

        def route(state: _State) -> str:
            return state["next"]

        builder = StateGraph(_State)
        builder.add_node("call_model", call_model)
        builder.add_node("run_tools", run_tools)
        builder.add_edge(START, "call_model")
        builder.add_conditional_edges(
            "call_model",
            route,
            {_CONTINUE_TOOLS: "run_tools", _CONTINUE_MODEL: "call_model", "end": END},
        )
        builder.add_edge("run_tools", "call_model")
        # Mỗi vòng model+tool tốn vài bước graph; chặn trên an toàn.
        return builder.compile().with_config({"recursion_limit": loop.max_iterations * 3 + 10})
