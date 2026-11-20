"""MCP client dùng SDK chính thức (mcp 2.x). Mỗi lần gọi mở một phiên ngắn (đơn giản, không rò
trạng thái giữa các người dùng).

stdio "sandbox" (ADR-0018): tiến trình con không thừa kế môi trường của ARIES — chỉ PATH/LANG,
proxy egress, HOME là thư mục tạm riêng và secret của chính server đó. Cách ly mạnh hơn
(gVisor/container riêng) ở Phase 7.
"""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx2
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client

from aiagent.modules.mcp.domain import (
    McpEndpoint,
    McpTool,
    McpTransport,
    risk_from_annotations,
)
from aiagent.shared_kernel.errors import AppError

logger = logging.getLogger(__name__)

MAX_TEXT_CHARS = 16_000
_PASSTHROUGH_ENV = ("PATH", "LANG", "LC_ALL", "PYTHONPATH", "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY")


def _root_cause(exc: BaseException) -> BaseException:
    """anyio gói lỗi trong ExceptionGroup — lấy lỗi gốc để báo rõ ràng."""
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]
    return exc


class McpError(AppError):
    code = "mcp_error"
    status_code = 502
    public_message = "MCP server không phản hồi đúng."


class SdkMcpClient:
    def __init__(self, timeout_s: float = 30.0) -> None:
        self._timeout = timeout_s

    @asynccontextmanager
    async def _session(self, endpoint: McpEndpoint) -> AsyncIterator[ClientSession]:
        if endpoint.transport is McpTransport.HTTP:
            if not endpoint.url:
                raise McpError("MCP server thiếu URL.")
            # Header bí mật (API key…) của chính server; ra Internet qua egress proxy (env).
            client = httpx2.AsyncClient(
                headers=dict(endpoint.secrets), timeout=httpx2.Timeout(self._timeout)
            )
            async with (
                client,
                streamable_http_client(endpoint.url, http_client=client) as streams,
            ):
                read, write = streams[0], streams[1]
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    yield session
            return
        if not endpoint.command:
            raise McpError("MCP server thiếu lệnh stdio.")
        with tempfile.TemporaryDirectory(prefix="aries-mcp-") as home:
            env = {k: v for k in _PASSTHROUGH_ENV if (v := os.environ.get(k))}
            env.update({"HOME": home, "TMPDIR": home})
            env.update(endpoint.secrets)
            params = StdioServerParameters(
                command=endpoint.command[0], args=endpoint.command[1:], env=env, cwd=home
            )
            with open(os.devnull, "w") as errlog:  # noqa: PTH123, ASYNC230 - bỏ stderr của server con
                async with (
                    stdio_client(params, errlog=errlog) as (read, write),
                    ClientSession(read, write) as session,
                ):
                    await session.initialize()
                    yield session

    async def _run(self, endpoint: McpEndpoint, op: Any) -> Any:
        try:
            async with asyncio.timeout(self._timeout):
                async with self._session(endpoint) as session:
                    return await op(session)
        except McpError:
            raise
        except TimeoutError as exc:
            raise McpError(f"MCP server quá thời gian ({self._timeout:.0f}s).") from exc
        except Exception as exc:  # lỗi mạng/giao thức/tiến trình con đủ loại
            cause = _root_cause(exc)
            if isinstance(cause, McpError):
                raise cause from exc
            detail = type(cause).__name__
            status = getattr(getattr(cause, "response", None), "status_code", None)
            if status:
                detail += f" HTTP {status}"
            logger.warning("MCP lỗi: %s", detail)
            raise McpError(f"Không kết nối được MCP server ({detail}).") from exc

    async def list_tools(self, endpoint: McpEndpoint) -> list[McpTool]:
        async def op(session: ClientSession) -> list[McpTool]:
            result = await session.list_tools()
            tools = []
            for t in result.tools:
                ann = t.annotations
                tools.append(
                    McpTool(
                        name=t.name,
                        description=(t.description or "")[:1024],
                        input_schema=dict(t.input_schema or {"type": "object"}),
                        risk=risk_from_annotations(
                            ann.read_only_hint if ann else None,
                            ann.destructive_hint if ann else None,
                        ),
                    )
                )
            return tools

        return list(await self._run(endpoint, op))

    async def call_tool(
        self, endpoint: McpEndpoint, name: str, arguments: dict[str, Any]
    ) -> tuple[str, bool]:
        async def op(session: ClientSession) -> tuple[str, bool]:
            result = await session.call_tool(name, arguments)
            content = getattr(result, "content", None)
            if content is None:
                return "MCP server yêu cầu tương tác thêm — chưa hỗ trợ.", True
            parts = []
            for block in content:
                text = getattr(block, "text", None)
                parts.append(text if isinstance(text, str) else f"[{getattr(block, 'type', '?')}]")
            text = "\n".join(parts) or "(không có nội dung)"
            return text[:MAX_TEXT_CHARS], bool(getattr(result, "is_error", False))

        text, is_error = await self._run(endpoint, op)
        return str(text), bool(is_error)
