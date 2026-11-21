"""Skill web: tìm kiếm qua SearXNG tự host + đọc nội dung một trang web (ADR-0016).

- Không gửi truy vấn tới dịch vụ tổng hợp bên ngoài ngoài các công cụ tìm kiếm mà SearXNG gọi.
- Kết quả là dữ liệu không tin cậy → core bọc nhãn và gắn taint cho run.
- web.fetch có chặn SSRF (ssrf.py), tự xử lý redirect (kiểm lại từng bước), giới hạn kích thước.

Cấu hình (AIAGENT_PLUGIN_CONFIG["web"]): searxng_url, language (mặc định "vi").
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin

import httpx

from aiagent.plugin_sdk import (
    PLUGIN_API_VERSION,
    HealthStatus,
    Plugin,
    PluginContext,
    PluginKind,
    PluginManifest,
    RiskLevel,
    ToolContext,
    ToolNotFoundError,
    ToolResult,
    ToolSpec,
)
from aiagent_plugins.skill_web.ssrf import BlockedUrlError, Resolver, check_url, system_resolver

MAX_BYTES = 2_000_000
MAX_TEXT_CHARS = 12_000
MAX_REDIRECTS = 3
USER_AGENT = "ARIES-agent/1.0 (+on-prem)"
_TEXT_TYPES = (
    "text/html",
    "text/plain",
    "application/json",
    "application/xml",
    "text/xml",
    "application/xhtml+xml",
    "text/markdown",
    "text/csv",
)

_MANIFEST = PluginManifest(
    id="web",
    kind=PluginKind.SKILL,
    name="Web",
    description="Tìm kiếm Internet (SearXNG tự host) và đọc nội dung trang web công khai.",
    version="1.0.0",
    api_version=PLUGIN_API_VERSION,
    tools=(
        ToolSpec(
            name="web.search",
            description=(
                "Tìm kiếm trên Internet. Trả về danh sách kết quả (tiêu đề, URL, trích đoạn). "
                "Dùng web.fetch để đọc chi tiết một kết quả."
            ),
            risk=RiskLevel.READ,
            untrusted_output=True,
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "minLength": 1, "maxLength": 400},
                    "max_results": {"type": "integer", "minimum": 1, "maximum": 10},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        ),
        ToolSpec(
            name="web.fetch",
            description="Đọc nội dung văn bản của một trang web công khai (http/https).",
            risk=RiskLevel.READ,
            untrusted_output=True,
            network=True,
            input_schema={
                "type": "object",
                "properties": {"url": {"type": "string", "minLength": 8, "maxLength": 2048}},
                "required": ["url"],
                "additionalProperties": False,
            },
        ),
    ),
    ui_hints={"icon": "search", "category": "web"},
)


class _TextExtractor(HTMLParser):
    _SKIP = frozenset({"script", "style", "noscript", "template", "svg", "head", "iframe"})
    _BLOCK = frozenset(
        {
            "p",
            "div",
            "br",
            "li",
            "tr",
            "h1",
            "h2",
            "h3",
            "h4",
            "h5",
            "h6",
            "section",
            "article",
            "header",
            "footer",
            "pre",
            "blockquote",
        }
    )

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title = ""
        self._skip = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "title":
            self._in_title = True
        if tag in self._SKIP:
            self._skip += 1
        elif tag in self._BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        if tag in self._SKIP and self._skip:
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        elif not self._skip:
            self.parts.append(data)


def html_to_text(html: str) -> tuple[str, str]:
    parser = _TextExtractor()
    parser.feed(html)
    parser.close()
    text = "".join(parser.parts)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return parser.title.strip(), text.strip()


class WebSkill(Plugin):
    def __init__(
        self, http: httpx.AsyncClient | None = None, resolver: Resolver = system_resolver
    ) -> None:
        self._http = http  # gọi SearXNG (nội bộ)
        self._fetch_http = http  # web.fetch (qua egress proxy nếu cấu hình)
        self._owns_http = http is None
        self._resolver: Resolver | None = resolver
        self._searxng_url: str | None = None
        self._language = "vi"

    @property
    def manifest(self) -> PluginManifest:
        return _MANIFEST

    async def on_load(self, ctx: PluginContext) -> None:
        url = ctx.config.get("searxng_url")
        self._searxng_url = str(url).rstrip("/") if url else None
        self._language = str(ctx.config.get("language", "vi"))
        if self._http is None:
            common: dict[str, Any] = {
                "follow_redirects": False,
                "timeout": httpx.Timeout(10.0, connect=5.0),
                "headers": {"User-Agent": USER_AGENT},
            }
            self._http = httpx.AsyncClient(trust_env=False, **common)
            proxy = ctx.config.get("fetch_proxy")
            if proxy:
                # Proxy phân giải DNS và chặn IP nội bộ — đóng lỗ DNS rebinding (ADR-0018).
                self._fetch_http = httpx.AsyncClient(proxy=str(proxy), trust_env=False, **common)
                self._resolver = None
            else:
                self._fetch_http = self._http

    async def on_unload(self) -> None:
        if self._owns_http:
            for client in {self._http, self._fetch_http}:
                if client is not None:
                    await client.aclose()

    @property
    def http(self) -> httpx.AsyncClient:
        if self._http is None:
            raise RuntimeError("WebSkill chưa được on_load")
        return self._http

    async def health_check(self) -> HealthStatus:
        if not self._searxng_url:
            return HealthStatus(False, "Chưa cấu hình searxng_url")
        return HealthStatus(True)

    async def call_tool(self, name: str, arguments: dict[str, Any], ctx: ToolContext) -> ToolResult:
        try:
            if name == "web.search":
                return await self._search(
                    str(arguments["query"]), int(arguments.get("max_results", 5))
                )
            if name == "web.fetch":
                return await self._fetch(str(arguments["url"]))
        except BlockedUrlError as exc:
            return ToolResult(f"URL bị chặn: {exc}", is_error=True)
        except httpx.ProxyError:
            return ToolResult(
                "URL bị chặn bởi egress proxy (đích nội bộ/riêng hoặc không được phép).",
                is_error=True,
            )
        except httpx.TimeoutException:
            return ToolResult("Hết thời gian chờ phản hồi.", is_error=True)
        except httpx.HTTPError as exc:
            return ToolResult(f"Lỗi mạng: {type(exc).__name__}", is_error=True)
        raise ToolNotFoundError(name)

    async def _search(self, query: str, max_results: int) -> ToolResult:
        if not self._searxng_url:
            return ToolResult("Chưa cấu hình máy chủ tìm kiếm (searxng_url).", is_error=True)
        r = await self.http.get(
            f"{self._searxng_url}/search",
            params={"q": query, "format": "json", "language": self._language, "safesearch": 1},
        )
        if r.status_code != 200:
            return ToolResult(f"Máy chủ tìm kiếm lỗi HTTP {r.status_code}.", is_error=True)
        results = (r.json().get("results") or [])[: max(1, min(max_results, 10))]
        if not results:
            return ToolResult(f"Không có kết quả cho: {query}")
        lines = []
        for i, item in enumerate(results, 1):
            title = str(item.get("title", "")).strip()[:200]
            url = str(item.get("url", ""))[:500]
            snippet = re.sub(r"\s+", " ", str(item.get("content", ""))).strip()[:400]
            lines.append(f"{i}. {title}\n   {url}\n   {snippet}")
        return ToolResult("\n".join(lines), data={"count": len(results)})

    async def _fetch(self, url: str) -> ToolResult:
        current = url
        for _ in range(MAX_REDIRECTS + 1):
            current = await check_url(current, self._resolver)
            fetcher = self._fetch_http or self.http
            async with fetcher.stream("GET", current) as r:
                if r.status_code in (301, 302, 303, 307, 308) and "location" in r.headers:
                    current = urljoin(current, r.headers["location"])
                    continue
                if r.status_code == 403 and "squid" in r.headers.get("server", "").lower():
                    return ToolResult(
                        "URL bị chặn bởi egress proxy (địa chỉ nội bộ).", is_error=True
                    )
                if r.status_code != 200:
                    return ToolResult(f"Trang trả về HTTP {r.status_code}.", is_error=True)
                ctype = r.headers.get("content-type", "").split(";")[0].strip().lower()
                if ctype and not ctype.startswith(_TEXT_TYPES):
                    return ToolResult(f"Không đọc được nội dung loại '{ctype}'.", is_error=True)
                body = bytearray()
                async for chunk in r.aiter_bytes():
                    body += chunk
                    if len(body) > MAX_BYTES:
                        break
                text = bytes(body[:MAX_BYTES]).decode(r.encoding or "utf-8", errors="replace")
            title = ""
            if ctype in ("text/html", "application/xhtml+xml") or "<html" in text[:500].lower():
                title, text = html_to_text(text)
            if len(text) > MAX_TEXT_CHARS:
                text = text[:MAX_TEXT_CHARS] + "\n…[đã cắt bớt]"
            header = f"Nguồn: {current}" + (f"\nTiêu đề: {title}" if title else "")
            return ToolResult(f"{header}\n\n{text}", data={"url": current})
        return ToolResult("Quá nhiều lần chuyển hướng.", is_error=True)
