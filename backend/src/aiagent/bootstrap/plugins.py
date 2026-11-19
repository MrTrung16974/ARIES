"""Nạp plugin từ Python entry points (ADR-0003).

Plugin lỗi hoặc không tương thích bị vô hiệu kèm lý do — không bao giờ làm sập core.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from enum import StrEnum
from importlib.metadata import EntryPoint, entry_points
from typing import Any, Protocol

from aiagent.plugin_sdk import (
    ENTRY_POINT_GROUPS,
    PLUGIN_API_VERSION,
    Plugin,
    PluginContext,
    PluginManifest,
)

logger = logging.getLogger(__name__)


class PluginStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"
    FAILED = "failed"


@dataclass
class LoadedPlugin:
    entry_point: str
    group: str
    status: PluginStatus
    manifest: PluginManifest | None = None
    instance: Plugin | None = None
    reason: str = ""


class _EntryPointLike(Protocol):
    name: str
    group: str
    value: str

    def load(self) -> Any: ...


EntryPointSource = Callable[[str], Iterable[_EntryPointLike]]


def _default_source(group: str) -> Iterable[EntryPoint]:
    return entry_points(group=group)


class PluginRegistry:
    def __init__(
        self,
        source: EntryPointSource = _default_source,
        disabled: Iterable[str] = (),
    ) -> None:
        self._source = source
        self._disabled = set(disabled)
        self._plugins: dict[str, LoadedPlugin] = {}
        self._rejected: list[LoadedPlugin] = []

    # ----------------------------------------------------------- loading
    def load_all(self) -> None:
        for group in ENTRY_POINT_GROUPS.values():
            for ep in self._source(group):
                self._load_one(ep, group)

    def _load_one(self, ep: _EntryPointLike, group: str) -> None:
        ref = f"{ep.group}:{ep.name}"
        try:
            obj = ep.load()
            instance = obj() if isinstance(obj, type) else obj
        except Exception as exc:  # plugin bên thứ ba có thể lỗi bất kỳ
            logger.exception("Không nạp được plugin %s", ref)
            self._reject(ref, group, PluginStatus.FAILED, f"load error: {type(exc).__name__}")
            return

        if not isinstance(instance, Plugin):
            self._reject(ref, group, PluginStatus.FAILED, "không phải aiagent.plugin_sdk.Plugin")
            return

        try:
            manifest = instance.manifest
        except Exception as exc:
            self._reject(ref, group, PluginStatus.FAILED, f"manifest error: {type(exc).__name__}")
            return

        if manifest.entry_point_group != group:
            self._reject(
                ref,
                group,
                PluginStatus.FAILED,
                f"kind '{manifest.kind}' không khớp nhóm entry point '{group}'",
                manifest,
            )
            return
        if manifest.api_version != PLUGIN_API_VERSION:
            self._reject(
                ref,
                group,
                PluginStatus.DISABLED,
                f"api_version {manifest.api_version} != core {PLUGIN_API_VERSION}",
                manifest,
            )
            return
        if manifest.id in self._disabled:
            self._reject(ref, group, PluginStatus.DISABLED, "bị tắt trong cấu hình", manifest)
            return
        if manifest.id in self._plugins:
            self._reject(ref, group, PluginStatus.FAILED, f"trùng id '{manifest.id}'", manifest)
            return

        self._plugins[manifest.id] = LoadedPlugin(
            entry_point=ref,
            group=group,
            status=PluginStatus.ACTIVE,
            manifest=manifest,
            instance=instance,
        )

    def _reject(
        self,
        ref: str,
        group: str,
        status: PluginStatus,
        reason: str,
        manifest: PluginManifest | None = None,
    ) -> None:
        logger.warning("Plugin %s bị %s: %s", ref, status, reason)
        self._rejected.append(
            LoadedPlugin(
                entry_point=ref, group=group, status=status, manifest=manifest, reason=reason
            )
        )

    # ----------------------------------------------------------- lifecycle
    async def start(self, ctx: PluginContext | None = None) -> None:
        ctx = ctx or PluginContext()
        for plugin_id, loaded in list(self._plugins.items()):
            if loaded.instance is None:
                continue
            try:
                await loaded.instance.on_load(ctx)
            except Exception as exc:
                logger.exception("on_load lỗi cho plugin %s", plugin_id)
                loaded.status = PluginStatus.FAILED
                loaded.reason = f"on_load error: {type(exc).__name__}"
                self._rejected.append(self._plugins.pop(plugin_id))

    async def stop(self) -> None:
        for loaded in self._plugins.values():
            if loaded.instance is None:
                continue
            try:
                await loaded.instance.on_unload()
            except Exception:
                logger.exception("on_unload lỗi cho plugin %s", loaded.entry_point)

    # ----------------------------------------------------------- queries
    def get(self, plugin_id: str) -> Plugin | None:
        loaded = self._plugins.get(plugin_id)
        return loaded.instance if loaded else None

    def active(self) -> list[LoadedPlugin]:
        return list(self._plugins.values())

    def all(self) -> list[LoadedPlugin]:
        return [*self._plugins.values(), *self._rejected]
