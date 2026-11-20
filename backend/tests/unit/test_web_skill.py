from __future__ import annotations

import httpx
import pytest

from aiagent.plugin_sdk import PluginContext, ToolContext
from aiagent_plugins.skill_web import WebSkill, html_to_text
from aiagent_plugins.skill_web.ssrf import BlockedUrlError, check_url

CTX = ToolContext(tenant_id="t", user_id="u", agent_id="a", run_id="r")

DNS = {
    "example.com": ["93.184.215.14"],
    "evil.test": ["10.0.0.5"],
    "mixed.test": ["93.184.215.14", "127.0.0.1"],
    "v6.test": ["::ffff:169.254.169.254"],
}


async def resolver(host: str, port: int) -> list[str]:
    if host in DNS:
        return DNS[host]
    try:  # IP literal
        import ipaddress

        return [str(ipaddress.ip_address(host))]
    except ValueError:
        raise OSError("nxdomain") from None


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "gopher://example.com/",
        "http://localhost:8080/",
        "http://127.0.0.1/",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]/",
        "http://evil.test/",
        "http://mixed.test/",
        "http://v6.test/",
        "http://example.com:22/",
        "http://user:pw@example.com/",
        "http://openbao.internal/",
        "http://nope.test/",
    ],
)
async def test_ssrf_blocked(url: str) -> None:
    with pytest.raises(BlockedUrlError):
        await check_url(url, resolver)


async def test_ssrf_allows_public() -> None:
    assert await check_url("https://example.com/a?b=1", resolver) == "https://example.com/a?b=1"


def test_html_to_text() -> None:
    title, text = html_to_text(
        "<html><head><title>Tin</title><script>evil()</script></head>"
        "<body><h1>Chào</h1><p>Nội&nbsp;dung</p><style>x{}</style></body></html>"
    )
    assert title == "Tin"
    assert "evil" not in text and "Chào" in text and "Nội\xa0dung" in text


async def _skill(handler: httpx.MockTransport, **config: str) -> WebSkill:
    skill = WebSkill(http=httpx.AsyncClient(transport=handler), resolver=resolver)
    await skill.on_load(PluginContext(config=dict(config)))
    return skill


async def test_search_uses_searxng() -> None:
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "results": [
                    {"title": "ARIES", "url": "https://example.com", "content": "nền tảng  agent"},
                ]
            },
        )

    skill = await _skill(httpx.MockTransport(handle), searxng_url="http://searxng:8080/")
    r = await skill.call_tool("web.search", {"query": "aries"}, CTX)
    assert not r.is_error and "1. ARIES" in r.content and "nền tảng agent" in r.content
    assert seen[0].url.params["format"] == "json" and seen[0].url.host == "searxng"

    unconfigured = await _skill(httpx.MockTransport(handle))
    r = await unconfigured.call_tool("web.search", {"query": "x"}, CTX)
    assert r.is_error


async def test_fetch_follows_safe_redirects_and_blocks_internal_ones() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/start":
            return httpx.Response(302, headers={"location": "/page"})
        if request.url.path == "/page":
            return httpx.Response(200, html="<title>T</title><p>Xin chào</p>")
        if request.url.path == "/to-internal":
            return httpx.Response(302, headers={"location": "http://127.0.0.1/admin"})
        if request.url.path == "/bin":
            return httpx.Response(200, content=b"\x00", headers={"content-type": "image/png"})
        return httpx.Response(404)

    skill = await _skill(httpx.MockTransport(handle))
    r = await skill.call_tool("web.fetch", {"url": "https://example.com/start"}, CTX)
    assert not r.is_error and "Tiêu đề: T" in r.content and "Xin chào" in r.content
    assert "https://example.com/page" in r.content

    r = await skill.call_tool("web.fetch", {"url": "https://example.com/to-internal"}, CTX)
    assert r.is_error and "bị chặn" in r.content
    r = await skill.call_tool("web.fetch", {"url": "https://example.com/bin"}, CTX)
    assert r.is_error and "image/png" in r.content
    r = await skill.call_tool("web.fetch", {"url": "https://example.com/missing"}, CTX)
    assert r.is_error and "404" in r.content
