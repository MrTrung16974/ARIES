from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class BuilderEvent:
    """Sự kiện stream của Architect (docs/agent-architect.md mục 8)."""

    type: str  # status|message|brief|clarify|spec_draft|spec_diff|validation|eval_progress|
    #           eval_result|test_result|confirm_required|usage|error|done
    data: dict[str, Any] = field(default_factory=dict)

    def to_sse(self) -> str:
        payload = json.dumps(self.data, ensure_ascii=False, default=str)
        return f"event: {self.type}\ndata: {payload}\n\n"
