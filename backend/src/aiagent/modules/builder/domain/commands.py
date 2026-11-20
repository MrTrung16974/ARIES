"""Slash command — xác định, không gọi LLM (docs/agent-architect.md, ADR-0013)."""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from enum import StrEnum


class CommandKind(StrEnum):
    HELP = "help"
    AGENT_NEW = "agent_new"
    AGENT_EDIT = "agent_edit"
    AGENT_LIST = "agent_list"
    AGENT_PUBLISH = "agent_publish"
    AGENT_ROLLBACK = "agent_rollback"
    AGENT_CHAT = "agent_chat"
    CONNECT = "connect"
    INVALID = "invalid"


@dataclass(frozen=True)
class Command:
    kind: CommandKind
    target: str | None = None  # tên agent
    text: str | None = None  # mô tả / yêu cầu
    version: int | None = None
    error: str | None = None


HELP_TEXT = """Lệnh có sẵn:
/agent new <mô tả>                 — tạo agent bằng mô tả (Agent Architect)
/agent edit "<tên>" <yêu cầu>      — sửa agent bằng lời
/agent list                        — liệt kê agent
/agent publish "<tên>" [version]   — publish version (mặc định bản mới nhất)
/agent rollback "<tên>" <version>  — publish lại một version cũ
/agent chat "<tên>"                — mở chat với agent
/connect [google|keycloak…]          — kết nối tài khoản qua OAuth (mở trang Kết nối)
Gõ không có dấu / sẽ được hiểu là /agent new <mô tả>."""


def _invalid(msg: str) -> Command:
    return Command(CommandKind.INVALID, error=msg)


def parse_command(raw: str) -> Command:
    text = raw.strip()
    if not text:
        return _invalid("Lệnh trống.")
    if not text.startswith("/"):
        return Command(CommandKind.AGENT_NEW, text=text)
    try:
        parts = shlex.split(text, posix=True)
    except ValueError:
        return _invalid("Thiếu dấu ngoặc kép đóng.")
    head = parts[0].lower()
    if head in ("/help", "/?"):
        return Command(CommandKind.HELP)
    if head == "/connect":
        provider = parts[1].lower() if len(parts) > 1 else None
        if provider and not provider.replace("_", "").isalnum():
            return _invalid("Tên nhà cung cấp không hợp lệ.")
        return Command(CommandKind.CONNECT, target=provider)
    if head != "/agent" or len(parts) < 2:
        return _invalid(f"Lệnh không hỗ trợ: {parts[0]}. Gõ /help.")

    sub, args = parts[1].lower(), parts[2:]
    if sub == "list":
        return Command(CommandKind.AGENT_LIST)
    if sub == "new":
        desc = text.split(None, 2)[2].strip() if len(text.split(None, 2)) > 2 else ""
        return Command(CommandKind.AGENT_NEW, text=desc) if desc else _invalid("Thiếu mô tả.")
    if sub == "edit":
        if len(args) < 2:
            return _invalid('Cú pháp: /agent edit "<tên>" <yêu cầu>')
        return Command(CommandKind.AGENT_EDIT, target=args[0], text=" ".join(args[1:]))
    if sub == "chat":
        return Command(CommandKind.AGENT_CHAT, target=args[0]) if args else _invalid("Thiếu tên.")
    if sub in ("publish", "rollback"):
        if not args:
            return _invalid("Thiếu tên agent.")
        version: int | None = None
        if len(args) > 1:
            if not args[1].lstrip("v").isdigit():
                return _invalid("Version phải là số, ví dụ 3 hoặc v3.")
            version = int(args[1].lstrip("v"))
        if sub == "rollback" and version is None:
            return _invalid('Cú pháp: /agent rollback "<tên>" <version>')
        kind = CommandKind.AGENT_PUBLISH if sub == "publish" else CommandKind.AGENT_ROLLBACK
        return Command(kind, target=args[0], version=version)
    return _invalid(f"Không có lệnh /agent {sub}. Gõ /help.")
