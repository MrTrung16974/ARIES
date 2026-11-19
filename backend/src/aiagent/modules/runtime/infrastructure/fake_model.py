"""Model giả, xác định — để dev/demo không cần API key và cho test.

- Tin user bắt đầu bằng `/tool <tên_tool_api> <json>` → gọi tool đó.
- Tin cuối là tool_result → tóm tắt kết quả.
- Còn lại → lặp lại tin nhắn.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from aiagent.modules.runtime.domain import (
    ModelEvent,
    ModelRequest,
    ModelTurn,
    TextDelta,
    ToolCallStarted,
    TurnCompleted,
)
from aiagent.shared_kernel.usage import Usage

FAKE_MODEL = "fake-echo"


class FakeChatModel:
    def __init__(self) -> None:
        self._counter = 0

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        last = request.messages[-1] if request.messages else {"role": "user", "content": []}
        raw = last.get("content", [])
        blocks: list[dict[str, Any]] = raw if isinstance(raw, list) else []
        tool_results = [b for b in blocks if b.get("type") == "tool_result"]
        text = "".join(str(b.get("text", "")) for b in blocks if b.get("type") == "text").strip()
        tool_names = {t.name for t in request.tools}
        usage = Usage(input_tokens=sum(len(str(m)) for m in request.messages) // 4, output_tokens=0)

        if tool_results:
            reply = "Kết quả tool: " + "; ".join(str(r.get("content", "")) for r in tool_results)
            async for ev in self._text(reply, usage):
                yield ev
            return

        if text.startswith("/tool "):
            parts = text.split(" ", 2)
            name = parts[1] if len(parts) > 1 else ""
            try:
                args = json.loads(parts[2]) if len(parts) > 2 else {}
            except json.JSONDecodeError:
                args = {"raw": parts[2]}
            if name in tool_names:
                self._counter += 1
                tool_id = f"toolu_fake_{self._counter}"
                yield ToolCallStarted(id=tool_id, name=name)
                yield TurnCompleted(
                    ModelTurn(
                        content=[{"type": "tool_use", "id": tool_id, "name": name, "input": args}],
                        stop_reason="tool_use",
                        usage=usage,
                        model=FAKE_MODEL,
                    )
                )
                return
            available = ", ".join(sorted(tool_names)) or "(không)"
            reply = f"Không có tool '{name}'. Tool hiện có: {available}"
        else:
            reply = f"Bạn vừa nói: {text}"
        async for ev in self._text(reply, usage):
            yield ev

    async def _text(self, reply: str, usage: Usage) -> AsyncIterator[ModelEvent]:
        for i in range(0, len(reply), 12):
            yield TextDelta(reply[i : i + 12])
        yield TurnCompleted(
            ModelTurn(
                content=[{"type": "text", "text": reply}],
                stop_reason="end_turn",
                usage=Usage(usage.input_tokens, len(reply) // 4),
                model=FAKE_MODEL,
            )
        )
