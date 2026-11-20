"""Danh mục model người dùng chọn được: tĩnh (Claude, cấu hình *_models) hoặc tự lấy từ
endpoint /models của provider (Ollama: model đã pull; Gemini/Groq/OpenRouter: model của key).
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from aiagent.modules.runtime.domain import ChatModelPort


@runtime_checkable
class ListsModels(Protocol):
    async def list_models(self) -> list[str]: ...


@dataclass(frozen=True)
class ModelEntry:
    provider: str
    model: str
    available: bool


# Thứ tự ưu tiên khi chọn model mặc định (Architect).
_PROVIDER_ORDER = ("anthropic", "gemini", "groq", "openrouter", "ollama", "fake")
_ANTHROPIC_ORDER = ("claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5")


class ModelCatalog:
    def __init__(
        self,
        allowed: Mapping[str, frozenset[str] | None],
        providers: Mapping[str, ChatModelPort],
    ) -> None:
        self._allowed = dict(allowed)
        self._providers = dict(providers)

    @property
    def allowed(self) -> dict[str, frozenset[str] | None]:
        return self._allowed

    async def _models_of(self, provider: str) -> list[str]:
        static = self._allowed.get(provider)
        if static is not None:
            if provider == "anthropic":
                return [m for m in _ANTHROPIC_ORDER if m in static] + sorted(
                    static - set(_ANTHROPIC_ORDER)
                )
            return sorted(static)
        impl = self._providers.get(provider)
        if isinstance(impl, ListsModels):
            return await impl.list_models()
        return []

    async def entries(self) -> list[ModelEntry]:
        names = sorted(self._allowed, key=lambda p: (_order(p), p))
        lists = await asyncio.gather(*(self._models_of(p) for p in names))
        return [
            ModelEntry(provider=p, model=m, available=p in self._providers)
            for p, models in zip(names, lists, strict=True)
            for m in models
        ]

    async def available(self) -> list[ModelEntry]:
        return [e for e in await self.entries() if e.available]


def _order(provider: str) -> int:
    return _PROVIDER_ORDER.index(provider) if provider in _PROVIDER_ORDER else len(_PROVIDER_ORDER)
