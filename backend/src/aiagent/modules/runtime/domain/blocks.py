"""Quy tắc xử lý content block của Messages API.

- Sau một lần fallback giữa chừng (server-side fallback), các block `thinking`, `redacted_thinking`,
  `tool_use` và `server_tool_use` không ghép cặp nằm TRƯỚC block `fallback` cuối cùng là của model
  đã từ chối: không được echo lại và không được thực thi. Block `fallback` giữ nguyên vị trí.
- Lịch sử là append-only; các hàm ở đây chỉ lọc bản gửi đi, không sửa dữ liệu đã lưu.
"""

from __future__ import annotations

from aiagent.modules.runtime.domain.model import ChatMessage, ContentBlock

_PRE_FALLBACK_DROP = {"thinking", "redacted_thinking", "tool_use"}


def _last_fallback_index(content: list[ContentBlock]) -> int:
    for i in range(len(content) - 1, -1, -1):
        if content[i].get("type") == "fallback":
            return i
    return -1


def _paired_server_tool_ids(content: list[ContentBlock]) -> set[str]:
    return {
        str(b.get("tool_use_id"))
        for b in content
        if str(b.get("type", "")).endswith("_tool_result") and b.get("type") != "tool_result"
    }


def sanitize_for_echo(content: list[ContentBlock]) -> list[ContentBlock]:
    boundary = _last_fallback_index(content)
    if boundary < 0:
        return content
    paired = _paired_server_tool_ids(content)
    kept: list[ContentBlock] = []
    for i, block in enumerate(content):
        kind = block.get("type")
        if i < boundary:
            if kind in _PRE_FALLBACK_DROP:
                continue
            if kind == "server_tool_use" and str(block.get("id")) not in paired:
                continue
        kept.append(block)
    return kept


def executable_tool_uses(content: list[ContentBlock]) -> list[ContentBlock]:
    """Các tool_use cần chạy: chỉ những block sau block `fallback` cuối (nếu có)."""
    boundary = _last_fallback_index(content)
    return [b for b in content[boundary + 1 :] if b.get("type") == "tool_use"]


def history_for_model(messages: list[ChatMessage]) -> list[ChatMessage]:
    """Bản lịch sử gửi cho model: lọc block theo quy tắc fallback, bỏ tin assistant rỗng."""
    out: list[ChatMessage] = []
    for m in messages:
        content = m["content"]
        if m["role"] == "assistant":
            content = sanitize_for_echo(content)
            if not content:
                continue
        out.append({"role": m["role"], "content": content})
    return out


def pending_tool_uses(messages: list[ChatMessage]) -> list[ContentBlock]:
    """tool_use của tin assistant cuối chưa có tool_result (run bị ngắt giữa chừng)."""
    if not messages or messages[-1]["role"] != "assistant":
        return []
    return executable_tool_uses(messages[-1]["content"])


def text_of(content: list[ContentBlock]) -> str:
    return "".join(str(b.get("text", "")) for b in content if b.get("type") == "text")
