"""Adapter OpenAI-compatible (Gemini, Groq, OpenRouter, Ollama) — không gọi mạng thật."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from aiagent.modules.runtime.application import ModelCatalog, ModelRouter
from aiagent.modules.runtime.domain import (
    ModelRateLimitedError,
    ModelRequest,
    ModelUnavailableError,
    StructuredRequest,
    TextDelta,
    ThinkingDelta,
    ToolCallStarted,
    ToolDefinition,
    TurnCompleted,
)
from aiagent.modules.runtime.infrastructure.openai_compat_model import (
    OpenAICompatChatModel,
    OpenAICompatConfig,
    to_openai_messages,
)


def _sse(*chunks: dict[str, Any]) -> bytes:
    body = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
    return body.encode()


def _model(
    handler: Any, provider: str = "ollama", **cfg: Any
) -> tuple[OpenAICompatChatModel, list[dict[str, Any]]]:
    seen: list[dict[str, Any]] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        if request.content:
            seen.append(json.loads(request.content))
        return handler(request)  # type: ignore[no-any-return]

    client = httpx.AsyncClient(transport=httpx.MockTransport(wrapped))
    config = OpenAICompatConfig(provider, "http://llm.test/v1", **cfg)
    return OpenAICompatChatModel(config, client=client), seen


def _req(**kw: Any) -> ModelRequest:
    return ModelRequest(
        model="qwen3:8b",
        system="sys",
        messages=[{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
        **kw,
    )


async def _collect(model: OpenAICompatChatModel, req: ModelRequest) -> list[Any]:
    return [e async for e in model.stream(req)]


def test_history_conversion() -> None:
    msgs = to_openai_messages(
        "sys",
        [
            {"role": "user", "content": [{"type": "text", "text": "giờ?"}]},
            {
                "role": "assistant",
                "content": [
                    {"type": "thinking", "thinking": "x", "signature": "s"},
                    {"type": "text", "text": "Để xem"},
                    {"type": "tool_use", "id": "t1", "name": "clock_now", "input": {"tz": "UTC"}},
                ],
            },
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "t1", "content": "10:00"},
                    {
                        "type": "tool_result",
                        "tool_use_id": "t2",
                        "content": [{"type": "text", "text": "hỏng"}],
                        "is_error": True,
                    },
                ],
            },
        ],
    )
    assert msgs[0] == {"role": "system", "content": "sys"}
    assert msgs[1] == {"role": "user", "content": "giờ?"}
    assert msgs[2]["content"] == "Để xem"
    assert msgs[2]["tool_calls"][0]["function"] == {
        "name": "clock_now",
        "arguments": '{"tz": "UTC"}',
    }
    assert msgs[3] == {"role": "tool", "tool_call_id": "t1", "content": "10:00"}
    assert msgs[4]["content"] == "LỖI: hỏng"
    assert len(msgs) == 5


async def test_stream_text_with_think_tags_and_usage() -> None:
    body = _sse(
        {"model": "qwen3:8b", "choices": [{"delta": {"content": "<thi"}}]},
        {"choices": [{"delta": {"content": "nk>suy nghĩ</think>Xin "}}]},
        {"choices": [{"delta": {"content": "chào"}, "finish_reason": "stop"}]},
        {"choices": [], "usage": {"prompt_tokens": 12, "completion_tokens": 5}},
    )
    model, seen = _model(lambda r: httpx.Response(200, content=body))
    events = await _collect(model, _req(effort="high"))

    text = "".join(e.text for e in events if isinstance(e, TextDelta))
    thinking = "".join(e.text for e in events if isinstance(e, ThinkingDelta))
    assert text == "Xin chào"
    assert thinking == "suy nghĩ"
    done = events[-1]
    assert isinstance(done, TurnCompleted)
    assert done.turn.content == [{"type": "text", "text": "Xin chào"}]
    assert done.turn.stop_reason == "end_turn"
    assert done.turn.usage.input_tokens == 12
    assert done.turn.usage.output_tokens == 5
    # Ollama không nhận reasoning_effort.
    assert "reasoning_effort" not in seen[0]
    assert seen[0]["stream"] is True


async def test_stream_tool_calls_are_assembled() -> None:
    body = _sse(
        {"choices": [{"delta": {"reasoning_content": "cần tool"}}]},
        {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [{"index": 0, "id": "c1", "function": {"name": "clock_now"}}]
                    }
                }
            ]
        },
        {
            "choices": [
                {"delta": {"tool_calls": [{"index": 0, "function": {"arguments": '{"tz"'}}]}}
            ]
        },
        {
            "choices": [
                {
                    "delta": {"tool_calls": [{"index": 0, "function": {"arguments": ': "UTC"}'}}]},
                    # Gemini có lúc trả "stop" dù có tool_calls.
                    "finish_reason": "stop",
                }
            ]
        },
    )
    model, seen = _model(
        lambda r: httpx.Response(200, content=body), "gemini", send_reasoning_effort=True
    )
    tools = [ToolDefinition("clock_now", "Giờ hiện tại", {"type": "object", "properties": {}})]
    events = await _collect(model, _req(tools=tools, effort="max"))

    assert any(isinstance(e, ThinkingDelta) for e in events)
    assert ToolCallStarted(id="c1", name="clock_now") in events
    done = events[-1]
    assert isinstance(done, TurnCompleted)
    assert done.turn.stop_reason == "tool_use"
    assert done.turn.content == [
        {"type": "tool_use", "id": "c1", "name": "clock_now", "input": {"tz": "UTC"}}
    ]
    assert seen[0]["reasoning_effort"] == "high"
    assert seen[0]["tools"][0]["function"]["name"] == "clock_now"


async def test_invalid_tool_json_retries_then_fails() -> None:
    body = _sse(
        {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {"index": 0, "id": "c1", "function": {"name": "x", "arguments": "{"}}
                        ]
                    },
                    "finish_reason": "tool_calls",
                }
            ]
        }
    )
    model, seen = _model(lambda r: httpx.Response(200, content=body))
    with pytest.raises(ModelUnavailableError):
        await _collect(model, _req())
    assert len(seen) == 3


@pytest.mark.parametrize(
    ("status", "exc", "fragment"),
    [
        (429, ModelRateLimitedError, ""),
        (401, ModelUnavailableError, "API key"),
        (404, ModelUnavailableError, "ollama pull qwen3:8b"),
        (500, ModelUnavailableError, ""),
    ],
)
async def test_http_errors_are_mapped(status: int, exc: type[Exception], fragment: str) -> None:
    model, _ = _model(lambda r: httpx.Response(status, json={"error": "x"}))
    with pytest.raises(exc) as info:
        await _collect(model, _req())
    assert fragment in str(info.value.args[0] if info.value.args else "")


async def test_connection_error_is_unavailable() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    model, _ = _model(boom)
    with pytest.raises(ModelUnavailableError):
        await _collect(model, _req())


async def test_generate_json_falls_back_to_json_object() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        kind = payload["response_format"]["type"]
        calls.append(kind)
        if kind == "json_schema":
            return httpx.Response(400, json={"error": "json_schema unsupported"})
        content = '```json\n{"name": "Bot"}\n```'
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 2},
            },
        )

    model, _ = _model(handler, "groq")
    result = await model.generate_json(
        StructuredRequest(model="m", system="s", prompt="p", schema={"type": "object"})
    )
    assert result.data == {"name": "Bot"}
    assert result.usage.output_tokens == 2
    assert calls == ["json_schema", "json_object"]


async def test_list_models_filters_and_caches() -> None:
    hits = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal hits
        hits += 1
        return httpx.Response(
            200, json={"data": [{"id": "a/b:free"}, {"id": "c/d"}, {"id": "models/e:free"}]}
        )

    model, _ = _model(handler, "openrouter", model_suffix=":free")
    assert await model.list_models() == ["a/b:free", "e:free"]
    assert await model.list_models() == ["a/b:free", "e:free"]
    assert hits == 1


async def test_catalog_static_and_discovered_models() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"id": "qwen3:8b"}, {"id": "llama3.2:3b"}]})

    ollama, _ = _model(handler)
    catalog = ModelCatalog(
        {"anthropic": frozenset({"claude-haiku-5-5", "claude-opus-5-5"}), "ollama": None},
        {"ollama": ollama},
    )
    entries = await catalog.entries()
    assert [(e.provider, e.model, e.available) for e in entries] == [
        ("anthropic", "claude-opus-5-5", False),
        ("anthropic", "claude-haiku-5-5", False),
        ("ollama", "llama3.2:3b", True),
        ("ollama", "qwen3:8b", True),
    ]
    assert [e.model for e in await catalog.available()] == ["llama3.2:3b", "qwen3:8b"]


def test_ollama_is_local_for_restricted_data() -> None:
    router = ModelRouter({"ollama": object(), "gemini": object()})  # type: ignore[dict-item]
    assert "ollama" in router._local
    assert "gemini" not in router._local
