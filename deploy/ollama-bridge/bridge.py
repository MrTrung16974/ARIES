"""Cầu nối HTTP: mạng nội bộ `app` → Ollama cài trên máy host (Windows/Mac/Linux).

API/worker nằm trong mạng internal (không ra ngoài được, kể cả máy host). Service này đứng ở cả
`app` và `edge`, chỉ chuyển tiếp tới đúng một đích: OLLAMA_UPSTREAM
(mặc định http://host.docker.internal:11434). ARIES cấu hình provider Ollama với base URL
http://ollama-host:11434.

Ollama trên host giữ nguyên chỉ nghe 127.0.0.1 (không lộ ra LAN). Khi đó Ollama chặn request có
header Host lạ (chống DNS rebinding) → cầu nối đặt lại `Host: localhost:11434`.
Dùng lại image backend (FastAPI/uvicorn/httpx) — không cần tải thêm gì.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import httpx
import uvicorn
from starlette.applications import Starlette
from starlette.background import BackgroundTask
from starlette.requests import Request
from starlette.responses import Response, StreamingResponse
from starlette.routing import Route

_RAW = os.environ.get("OLLAMA_UPSTREAM", "http://host.docker.internal:11434").rstrip("/")
UPSTREAM = _RAW if "://" in _RAW else f"http://{_RAW}"
HOST_HEADER = os.environ.get("OLLAMA_HOST_HEADER", "localhost:11434")
_HOP = {"host", "connection", "keep-alive", "transfer-encoding", "te", "upgrade", "content-length"}

client = httpx.AsyncClient(
    base_url=UPSTREAM, timeout=httpx.Timeout(600.0, connect=5.0), trust_env=False
)


async def proxy(request: Request) -> Response:
    headers = {k: v for k, v in request.headers.items() if k.lower() not in _HOP}
    headers["host"] = HOST_HEADER
    headers.pop("origin", None)  # Ollama chặn Origin lạ; cầu nối không phục vụ trình duyệt
    upstream_req = client.build_request(
        request.method,
        request.url.path,
        params=request.query_params,
        headers=headers,
        content=await request.body(),
    )
    try:
        upstream = await client.send(upstream_req, stream=True)
    except httpx.HTTPError as exc:
        return Response(
            f"Không kết nối được Ollama trên host ({type(exc).__name__}: {exc}).", status_code=502
        )

    async def body() -> AsyncIterator[bytes]:
        async for chunk in upstream.aiter_raw():
            yield chunk

    out_headers = {k: v for k, v in upstream.headers.items() if k.lower() not in _HOP}
    return StreamingResponse(
        body(),
        status_code=upstream.status_code,
        headers=out_headers,
        background=BackgroundTask(upstream.aclose),
    )


app = Starlette(
    routes=[Route("/{path:path}", proxy, methods=["GET", "POST", "PUT", "DELETE", "HEAD"])]
)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("LISTEN_PORT", "11434")))  # noqa: S104
