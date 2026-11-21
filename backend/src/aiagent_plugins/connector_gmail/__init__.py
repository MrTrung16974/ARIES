"""Connector Gmail (ADR-0021): tool đọc/soạn/gửi mail + trigger mail mới (polling History API).

- OAuth Google qua Credential Broker: plugin chỉ nhận `access_token` đã làm mới.
- Nội dung mail là dữ liệu KHÔNG tin cậy (`untrusted_output`): core bọc nhãn và đánh dấu taint.
- HTML → văn bản thuần; không tải ảnh, không mở link.
- `gmail.send` là `external_send` → luôn cần người duyệt.
"""

from __future__ import annotations

import base64
import html
import json
import re
from email.message import EmailMessage
from typing import Any

import httpx

from aiagent.plugin_sdk import (
    PLUGIN_API_VERSION,
    ConnectionContext,
    HealthStatus,
    Plugin,
    PluginKind,
    PluginManifest,
    RiskLevel,
    ToolContext,
    ToolNotFoundError,
    ToolResult,
    ToolSpec,
    TriggerEvent,
    TriggerSpec,
)

API = "https://gmail.googleapis.com/gmail/v1/users/me"
MAX_BODY = 15_000
MAX_EVENTS = 10
_SKIP_LABELS = {"DRAFT", "SENT", "SPAM", "TRASH"}
_ID = r"^[A-Za-z0-9_-]{6,64}$"
_EMAIL = {"type": "string", "pattern": r"^[^@\s<>,;\"]+@[^@\s<>,;\"]+\.[^@\s<>,;\"]+$"}
_RECIPIENTS = {"type": "array", "items": _EMAIL, "minItems": 1, "maxItems": 20}
_COMPOSE: dict[str, Any] = {
    "type": "object",
    "properties": {
        "to": _RECIPIENTS,
        "cc": {**_RECIPIENTS, "minItems": 0},
        "subject": {"type": "string", "maxLength": 250, "pattern": r"^[^\r\n]*$"},
        "body": {"type": "string", "minLength": 1, "maxLength": 50_000},
        "reply_to_message_id": {"type": "string", "pattern": _ID},
    },
    "required": ["to", "subject", "body"],
    "additionalProperties": False,
}


class GmailError(Exception):
    pass


def html_to_text(raw: str) -> str:
    text = re.sub(r"(?is)<(script|style|head)[^>]*>.*?</\1>", " ", raw)
    text = re.sub(r"(?i)<br\s*/?>|</(p|div|li|tr|h[1-6])>", "\n", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text)
    lines = [re.sub(r"[ \t ]+", " ", line).strip() for line in text.splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _b64(data: str) -> str:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", "replace")


def _body(payload: dict[str, Any]) -> str:
    """Ưu tiên text/plain; không có thì chuyển text/html sang text."""
    plain: list[str] = []
    rich: list[str] = []

    def walk(part: dict[str, Any]) -> None:
        mime = str(part.get("mimeType", ""))
        data = (part.get("body") or {}).get("data")
        if data and mime == "text/plain":
            plain.append(_b64(data))
        elif data and mime == "text/html":
            rich.append(_b64(data))
        for child in part.get("parts") or []:
            walk(child)

    walk(payload)
    text = "\n".join(plain).strip() or html_to_text("\n".join(rich))
    return text if len(text) <= MAX_BODY else text[:MAX_BODY] + "\n…(đã cắt bớt)"


def _headers(payload: dict[str, Any]) -> dict[str, str]:
    return {
        str(h.get("name", "")).lower(): str(h.get("value", ""))
        for h in payload.get("headers") or []
    }


def _render(msg: dict[str, Any]) -> tuple[str, str]:
    payload = msg.get("payload") or {}
    h = _headers(payload)
    subject = h.get("subject", "(không tiêu đề)")
    text = (
        f"Từ: {h.get('from', '')}\nTới: {h.get('to', '')}\nNgày: {h.get('date', '')}\n"
        f"Tiêu đề: {subject}\nID: {msg.get('id', '')}\n\n{_body(payload)}"
    )
    return subject, text


class GmailConnector(Plugin):
    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._transport = transport  # test truyền MockTransport; mặc định đi qua egress proxy

    @property
    def manifest(self) -> PluginManifest:
        return PluginManifest(
            id="gmail",
            kind=PluginKind.CONNECTOR,
            name="Gmail",
            description="Đọc, soạn nháp và gửi mail (gửi luôn cần duyệt); trigger khi có mail mới.",
            version="1.0.0",
            api_version=PLUGIN_API_VERSION,
            config_schema={"type": "object"},
            oauth_provider="google",
            oauth_scopes=(
                "https://www.googleapis.com/auth/gmail.readonly",
                "https://www.googleapis.com/auth/gmail.compose",
                "https://www.googleapis.com/auth/gmail.send",
            ),
            tools=(
                ToolSpec(
                    name="gmail.search",
                    description="Tìm mail theo cú pháp tìm kiếm Gmail (from:, subject:, newer_than:…).",
                    risk=RiskLevel.READ,
                    untrusted_output=True,
                    input_schema={
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "maxLength": 200},
                            "max_results": {"type": "integer", "minimum": 1, "maximum": 20},
                        },
                        "required": ["query"],
                        "additionalProperties": False,
                    },
                ),
                ToolSpec(
                    name="gmail.read_message",
                    description="Đọc toàn văn một mail theo ID.",
                    risk=RiskLevel.READ,
                    untrusted_output=True,
                    input_schema={
                        "type": "object",
                        "properties": {"message_id": {"type": "string", "pattern": _ID}},
                        "required": ["message_id"],
                        "additionalProperties": False,
                    },
                ),
                ToolSpec(
                    name="gmail.create_draft",
                    description="Soạn thư nháp (không gửi). Có thể trả lời một mail qua reply_to_message_id.",
                    risk=RiskLevel.WRITE,
                    input_schema=_COMPOSE,
                ),
                ToolSpec(
                    name="gmail.send",
                    description="Gửi mail ra ngoài — luôn cần người duyệt.",
                    risk=RiskLevel.EXTERNAL_SEND,
                    input_schema=_COMPOSE,
                ),
            ),
            triggers=(
                TriggerSpec(
                    name="gmail.new_message",
                    description="Có mail mới trong hộp thư đến.",
                    config_schema={
                        "type": "object",
                        "properties": {
                            "from_contains": {"type": "string", "maxLength": 100},
                            "subject_contains": {"type": "string", "maxLength": 100},
                        },
                        "additionalProperties": False,
                    },
                ),
            ),
            ui_hints={"icon": "mail", "category": "email"},
        )

    # ------------------------------------------------------------------ HTTP
    async def _api(
        self,
        conn: ConnectionContext,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        token = conn.secrets.get("access_token")
        if not token:
            raise GmailError("Kết nối Gmail chưa được ủy quyền.")
        async with httpx.AsyncClient(timeout=20.0, transport=self._transport) as http:
            try:
                r = await http.request(
                    method,
                    f"{API}{path}",
                    params=params,
                    json=body,
                    headers={"Authorization": f"Bearer {token}"},
                )
            except httpx.HTTPError as exc:
                raise GmailError(f"Không kết nối được Gmail ({type(exc).__name__}).") from None
        if r.status_code == 401:
            raise GmailError("Token Gmail không còn hiệu lực — hãy kết nối lại.")
        if r.status_code == 404:
            raise LookupError(path)
        if r.status_code == 429:
            raise GmailError("Gmail đang giới hạn tần suất (429) — thử lại sau.")
        if r.status_code >= 400:
            raise GmailError(f"Gmail trả về HTTP {r.status_code}.")
        data = r.json()
        return data if isinstance(data, dict) else {}

    # ------------------------------------------------------------------ tool
    async def call_tool(self, name: str, arguments: dict[str, Any], ctx: ToolContext) -> ToolResult:
        if ctx.connection is None:
            return ToolResult("Tool này cần một kết nối Gmail.", is_error=True)
        conn = ctx.connection
        try:
            if name == "gmail.search":
                return await self._search(conn, arguments)
            if name == "gmail.read_message":
                msg = await self._api(
                    conn, "GET", f"/messages/{arguments['message_id']}", params={"format": "full"}
                )
                return ToolResult(_render(msg)[1])
            if name in ("gmail.create_draft", "gmail.send"):
                return await self._compose(conn, arguments, send=name == "gmail.send")
        except LookupError:
            return ToolResult("Không tìm thấy mail.", is_error=True)
        except GmailError as exc:
            return ToolResult(str(exc), is_error=True)
        raise ToolNotFoundError(name)

    async def _search(self, conn: ConnectionContext, args: dict[str, Any]) -> ToolResult:
        found = await self._api(
            conn,
            "GET",
            "/messages",
            params={"q": args["query"], "maxResults": int(args.get("max_results", 10))},
        )
        rows = []
        for item in found.get("messages") or []:
            msg = await self._api(
                conn,
                "GET",
                f"/messages/{item['id']}",
                params={"format": "metadata", "metadataHeaders": ["From", "Subject", "Date"]},
            )
            h = _headers(msg.get("payload") or {})
            rows.append(
                {
                    "id": msg.get("id"),
                    "from": h.get("from", ""),
                    "subject": h.get("subject", ""),
                    "date": h.get("date", ""),
                    "snippet": html.unescape(str(msg.get("snippet", "")))[:300],
                }
            )
        if not rows:
            return ToolResult("Không có mail nào khớp.")
        return ToolResult(json.dumps(rows, ensure_ascii=False))

    async def _compose(
        self, conn: ConnectionContext, args: dict[str, Any], *, send: bool
    ) -> ToolResult:
        mail = EmailMessage()
        mail["To"] = ", ".join(args["to"])
        if args.get("cc"):
            mail["Cc"] = ", ".join(args["cc"])
        subject = str(args["subject"])
        thread_id = None
        reply_id = args.get("reply_to_message_id")
        if reply_id:
            orig = await self._api(
                conn,
                "GET",
                f"/messages/{reply_id}",
                params={"format": "metadata", "metadataHeaders": ["Message-ID", "References"]},
            )
            h = _headers(orig.get("payload") or {})
            thread_id = orig.get("threadId")
            if h.get("message-id"):
                mail["In-Reply-To"] = h["message-id"]
                mail["References"] = f"{h.get('references', '')} {h['message-id']}".strip()
        mail["Subject"] = subject
        mail.set_content(str(args["body"]))
        raw = base64.urlsafe_b64encode(mail.as_bytes()).decode("ascii")
        message: dict[str, Any] = {"raw": raw}
        if thread_id:
            message["threadId"] = thread_id
        if send:
            sent = await self._api(conn, "POST", "/messages/send", body=message)
            return ToolResult(f"Đã gửi mail (ID {sent.get('id', '?')}).")
        draft = await self._api(conn, "POST", "/drafts", body={"message": message})
        return ToolResult(f"Đã tạo thư nháp (ID {draft.get('id', '?')}) — mở Gmail để xem và gửi.")

    # ------------------------------------------------------------------ trigger
    async def poll_trigger(
        self,
        name: str,
        connection: ConnectionContext,
        state: dict[str, Any],
        config: dict[str, Any],
    ) -> tuple[list[TriggerEvent], dict[str, Any]]:
        if name != "gmail.new_message":
            raise NotImplementedError(name)
        start = state.get("history_id")
        if not start:  # lần đầu: chỉ ghi mốc, không xử lý mail cũ
            profile = await self._api(connection, "GET", "/profile")
            return [], {"history_id": str(profile.get("historyId", ""))}
        ids: list[str] = []
        latest = str(start)
        page: str | None = None
        try:
            for _ in range(3):
                params: dict[str, Any] = {
                    "startHistoryId": start,
                    "historyTypes": "messageAdded",
                    "labelId": "INBOX",
                    "maxResults": 100,
                }
                if page:
                    params["pageToken"] = page
                res = await self._api(connection, "GET", "/history", params=params)
                latest = str(res.get("historyId") or latest)
                for h in res.get("history") or []:
                    for added in h.get("messagesAdded") or []:
                        mid = str((added.get("message") or {}).get("id", ""))
                        if mid and mid not in ids:
                            ids.append(mid)
                page = res.get("nextPageToken")
                if not page:
                    break
        except LookupError:  # historyId quá cũ → đặt lại mốc
            profile = await self._api(connection, "GET", "/profile")
            return [], {"history_id": str(profile.get("historyId", ""))}

        want_from = str(config.get("from_contains") or "").lower()
        want_subject = str(config.get("subject_contains") or "").lower()
        events: list[TriggerEvent] = []
        for mid in ids[-MAX_EVENTS:]:
            try:
                msg = await self._api(
                    connection, "GET", f"/messages/{mid}", params={"format": "full"}
                )
            except LookupError:
                continue  # đã bị xóa
            if _SKIP_LABELS & set(msg.get("labelIds") or []):
                continue
            h = _headers(msg.get("payload") or {})
            if want_from and want_from not in h.get("from", "").lower():
                continue
            if want_subject and want_subject not in h.get("subject", "").lower():
                continue
            subject, text = _render(msg)
            events.append(TriggerEvent(id=f"gmail:{mid}", title=subject[:200], content=text))
        return events, {"history_id": latest}

    async def test_connection(self, connection: ConnectionContext) -> HealthStatus:
        try:
            profile = await self._api(connection, "GET", "/profile")
        except (GmailError, LookupError) as exc:
            return HealthStatus(False, str(exc) or "Không gọi được Gmail.")
        return HealthStatus(True, f"Kết nối OK — hộp thư {profile.get('emailAddress', '')}.")
