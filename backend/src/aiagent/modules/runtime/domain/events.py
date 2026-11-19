"""RunEvent — contract stream giữa runtime và mọi client (web, Telegram...). ADR-0005."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class RunEventType(StrEnum):
    RUN_STARTED = "run_started"
    TEXT = "token"  # đoạn văn bản stream; giá trị wire giữ "token" (ADR-0005)
    THINKING = "thinking"
    TOOL_START = "tool_start"
    TOOL_END = "tool_end"
    MESSAGE = "message"
    USAGE = "usage"
    ERROR = "error"
    DONE = "done"


@dataclass(frozen=True)
class RunEvent:
    type: RunEventType
    data: dict[str, Any] = field(default_factory=dict)

    def to_sse(self) -> str:
        payload = json.dumps(self.data, ensure_ascii=False, default=str)
        return f"event: {self.type.value}\ndata: {payload}\n\n"
