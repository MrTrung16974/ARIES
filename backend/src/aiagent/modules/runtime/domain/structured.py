"""Port sinh JSON theo schema (structured outputs) — dùng cho Agent Architect, eval judge."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from aiagent.shared_kernel.usage import Usage

# Từ khóa JSON Schema mà structured outputs không hỗ trợ (validate lại bằng Pydantic phía client).
_UNSUPPORTED_KEYS = frozenset(
    {
        "minLength",
        "maxLength",
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "multipleOf",
        "minItems",
        "maxItems",
        "uniqueItems",
        "pattern",
        "default",
        "title",
    }
)


@dataclass(frozen=True)
class StructuredRequest:
    model: str
    system: str
    prompt: str
    schema: dict[str, Any]
    effort: str = "medium"
    max_tokens: int = 16000


@dataclass(frozen=True)
class StructuredResult:
    data: dict[str, Any]
    usage: Usage = field(default_factory=Usage)


class StructuredModelPort(Protocol):
    async def generate_json(self, request: StructuredRequest) -> StructuredResult: ...


def strict_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Chuẩn hóa JSON Schema (thường từ Pydantic) cho structured outputs.

    - bỏ từ khóa không hỗ trợ; mọi object `additionalProperties: false` và mọi property `required`.
    """

    def walk(node: Any) -> Any:
        if isinstance(node, list):
            return [walk(n) for n in node]
        if not isinstance(node, dict):
            return node
        out = {k: walk(v) for k, v in node.items() if k not in _UNSUPPORTED_KEYS}
        if out.get("type") == "object" or "properties" in out:
            props = out.get("properties", {})
            out["additionalProperties"] = False
            out["required"] = list(props)
        return out

    result = walk(schema)
    if not isinstance(result, dict):
        raise TypeError("schema phải là object JSON")
    return result
