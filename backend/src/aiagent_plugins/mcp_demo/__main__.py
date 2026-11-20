"""MCP server mẫu của ARIES — để kiểm thử MCP bridge (stdio và streamable HTTP).

python -m aiagent_plugins.mcp_demo                 # stdio
python -m aiagent_plugins.mcp_demo --http 8765     # streamable HTTP (yêu cầu header X-Api-Key)
"""

from __future__ import annotations

import os
import sys
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations

server = MCPServer(name="aries-demo", version="1.0.0")


@server.tool(description="Cộng hai số.", annotations=ToolAnnotations(readOnlyHint=True))
def add(a: float, b: float) -> str:
    return str(a + b)


@server.tool(
    description="Tra tỷ giá mẫu (dữ liệu giả lập) của một mã tiền tệ so với VND.",
    annotations=ToolAnnotations(readOnlyHint=True),
)
def exchange_rate(currency: str) -> str:
    rates = {"USD": 25_400, "EUR": 27_300, "JPY": 168}
    code = currency.upper()
    return f"1 {code} = {rates[code]:,} VND" if code in rates else f"Không có tỷ giá {code}"


@server.tool(
    description="Tạo phiếu yêu cầu (giả lập) — thao tác ghi.",
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False),
)
def create_ticket(title: str) -> str:
    return f"Đã tạo phiếu: {title}"


@server.tool(
    description="Báo biến môi trường bí mật có được truyền vào không (không in giá trị).",
    annotations=ToolAnnotations(readOnlyHint=True),
)
def env_check() -> str:
    return "DEMO_TOKEN=" + ("có" if os.environ.get("DEMO_TOKEN") else "không")


def _serve_http(port: int) -> None:
    """Streamable HTTP; nếu đặt DEMO_API_KEY thì bắt buộc header X-Api-Key khớp."""
    import uvicorn

    # Giữ chống DNS rebinding của SDK, chỉ cho phép tên dịch vụ nội bộ (DEMO_ALLOWED_HOSTS).
    hosts = os.environ.get("DEMO_ALLOWED_HOSTS", f"mcp-demo:{port},localhost:{port}")
    app = server.streamable_http_app(
        host="0.0.0.0",  # noqa: S104
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=[h.strip() for h in hosts.split(",") if h.strip()],
        ),
    )
    key = os.environ.get("DEMO_API_KEY", "")

    async def guarded(scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] == "http" and key:
            headers = dict(scope.get("headers") or [])
            if headers.get(b"x-api-key", b"").decode() != key:
                await send({"type": "http.response.start", "status": 401, "headers": []})
                await send({"type": "http.response.body", "body": b"unauthorized"})
                return
        await app(scope, receive, send)

    uvicorn.run(guarded, host="0.0.0.0", port=port, log_level="warning")  # noqa: S104


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "--http":
        _serve_http(int(sys.argv[2]))
    else:
        server.run("stdio")
