from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import pytest
from pydantic import ValidationError

from aiagent.bootstrap.plugins import PluginRegistry, PluginStatus
from aiagent.plugin_sdk import PLUGIN_API_VERSION, Plugin, PluginKind, PluginManifest
from aiagent_plugins.skill_echo import EchoSkill


def manifest(**overrides: Any) -> PluginManifest:
    data: dict[str, Any] = {
        "id": "demo",
        "kind": PluginKind.SKILL,
        "name": "Demo",
        "version": "1.0.0",
        "api_version": PLUGIN_API_VERSION,
    }
    data.update(overrides)
    return PluginManifest(**data)


def plugin_cls(m: PluginManifest, on_load: Callable[[], None] | None = None) -> type[Plugin]:
    class _P(Plugin):
        @property
        def manifest(self) -> PluginManifest:
            return m

        async def on_load(self, ctx: Any) -> None:
            if on_load:
                on_load()

    return _P


@dataclass
class FakeEP:
    name: str
    group: str
    target: Any
    value: str = "fake:target"

    def load(self) -> Any:
        if isinstance(self.target, Exception):
            raise self.target
        return self.target


def registry(*eps: FakeEP, disabled: tuple[str, ...] = ()) -> PluginRegistry:
    def source(group: str) -> list[FakeEP]:
        return [e for e in eps if e.group == group]

    reg = PluginRegistry(source=source, disabled=disabled)
    reg.load_all()
    return reg


def status_of(reg: PluginRegistry, ref: str) -> tuple[PluginStatus, str]:
    found = next(p for p in reg.all() if p.entry_point == ref)
    return found.status, found.reason


# ---------------------------------------------------------------- manifest
@pytest.mark.parametrize(
    "overrides",
    [{"id": "Bad-Id"}, {"version": "1.0"}, {"api_version": 0}, {"unknown_field": 1}],
)
def test_manifest_validation(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        manifest(**overrides)


def test_builtin_echo_manifest_is_valid() -> None:
    m = EchoSkill().manifest
    assert m.id == "echo"
    assert m.entry_point_group == "aiagent.skills"
    assert m.tools[0].risk == "read"


# ---------------------------------------------------------------- loader
def test_loads_valid_plugin() -> None:
    reg = registry(FakeEP("demo", "aiagent.skills", plugin_cls(manifest())))
    assert [p.manifest.id for p in reg.active() if p.manifest] == ["demo"]
    assert isinstance(reg.get("demo"), Plugin)


def test_incompatible_api_version_is_disabled() -> None:
    reg = registry(FakeEP("demo", "aiagent.skills", plugin_cls(manifest(api_version=99))))
    assert reg.active() == []
    status, reason = status_of(reg, "aiagent.skills:demo")
    assert status is PluginStatus.DISABLED
    assert "api_version" in reason


def test_kind_must_match_group() -> None:
    reg = registry(FakeEP("demo", "aiagent.connectors", plugin_cls(manifest())))
    assert status_of(reg, "aiagent.connectors:demo")[0] is PluginStatus.FAILED


def test_load_error_does_not_crash() -> None:
    reg = registry(
        FakeEP("broken", "aiagent.skills", ImportError("boom")),
        FakeEP("demo", "aiagent.skills", plugin_cls(manifest())),
    )
    assert status_of(reg, "aiagent.skills:broken")[0] is PluginStatus.FAILED
    assert reg.get("demo") is not None


def test_non_plugin_object_is_rejected() -> None:
    reg = registry(FakeEP("weird", "aiagent.skills", object()))
    assert status_of(reg, "aiagent.skills:weird")[0] is PluginStatus.FAILED


def test_disabled_by_config() -> None:
    reg = registry(FakeEP("demo", "aiagent.skills", plugin_cls(manifest())), disabled=("demo",))
    assert reg.get("demo") is None
    assert status_of(reg, "aiagent.skills:demo")[0] is PluginStatus.DISABLED


def test_duplicate_id_keeps_first() -> None:
    reg = registry(
        FakeEP("a", "aiagent.skills", plugin_cls(manifest())),
        FakeEP("b", "aiagent.skills", plugin_cls(manifest())),
    )
    assert len(reg.active()) == 1
    assert status_of(reg, "aiagent.skills:b")[0] is PluginStatus.FAILED


async def test_on_load_failure_moves_plugin_to_failed() -> None:
    def explode() -> None:
        raise RuntimeError("bad config")

    reg = registry(FakeEP("demo", "aiagent.skills", plugin_cls(manifest(), on_load=explode)))
    await reg.start()
    assert reg.get("demo") is None
    assert status_of(reg, "aiagent.skills:demo")[0] is PluginStatus.FAILED


def test_real_entry_points_include_echo() -> None:
    reg = PluginRegistry()
    reg.load_all()
    assert reg.get("echo") is not None
