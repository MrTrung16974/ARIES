"""Manifest agent (ADR-0022): đọc/ghi YAML an toàn, đổi tham chiếu UUID ⇄ tên. Hàm thuần."""

from __future__ import annotations

import copy
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

import yaml

API_VERSION = "aries.ai/v1"
KIND = "Agent"
MAX_MANIFEST_BYTES = 256 * 1024


class ManifestError(ValueError):
    """Manifest sai — thông điệp chỉ rõ đường dẫn (vd `spec.connections[0]`)."""


@dataclass(frozen=True)
class Refs:
    """Danh mục tên ⇄ id người dùng nhìn thấy: connection, KB, MCP server, agent."""

    connections: dict[UUID, str] = field(default_factory=dict)
    knowledge_bases: dict[UUID, str] = field(default_factory=dict)
    mcp_servers: dict[UUID, str] = field(default_factory=dict)
    agents: dict[UUID, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Template:
    id: str
    title: str
    description: str
    category: str
    manifest: dict[str, Any]


def load_manifest(text: str) -> dict[str, Any]:
    if len(text.encode("utf-8")) > MAX_MANIFEST_BYTES:
        raise ManifestError("Manifest quá lớn (tối đa 256 KB).")
    try:
        doc = yaml.safe_load(text)  # chỉ kiểu cơ bản — không tạo được object Python
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        where = f" (dòng {mark.line + 1})" if mark is not None else ""
        raise ManifestError(f"YAML không hợp lệ{where}.") from None
    if not isinstance(doc, dict):
        raise ManifestError("Manifest phải là một object (mapping).")
    if doc.get("apiVersion") != API_VERSION:
        raise ManifestError(f"apiVersion phải là {API_VERSION!r}.")
    if doc.get("kind") != KIND:
        raise ManifestError(f"kind phải là {KIND!r}.")
    if not isinstance(doc.get("spec"), dict):
        raise ManifestError("Thiếu `spec`.")
    meta = doc.get("metadata") or {}
    if not isinstance(meta, dict):
        raise ManifestError("`metadata` phải là object.")
    return doc


def dump_manifest(doc: dict[str, Any]) -> str:
    return yaml.safe_dump(doc, allow_unicode=True, sort_keys=False, width=100)


def _lookup(kind: str, path: str, value: Any, table: dict[UUID, str]) -> str:
    """Tên hoặc UUID → UUID (chuỗi). Lỗi nếu không thấy / trùng tên."""
    text = str(value).strip()
    try:
        uid = UUID(text)
    except ValueError:
        uid = None
    if uid is not None:
        if uid not in table:
            raise ManifestError(
                f"{path}: không tìm thấy {kind} có id {text} (hoặc bạn không có quyền)."
            )
        return str(uid)
    matches = [i for i, name in table.items() if name == text]
    if not matches:
        raise ManifestError(
            f"{path}: không tìm thấy {kind} tên {text!r} (hoặc bạn không có quyền)."
        )
    if len(matches) > 1:
        raise ManifestError(f"{path}: có {len(matches)} {kind} cùng tên {text!r} — hãy dùng id.")
    return str(matches[0])


def _ref_of(item: dict[str, Any], name_key: str, id_key: str, path: str) -> Any:
    if id_key in item:
        return item.pop(id_key)
    if name_key in item:
        return item.pop(name_key)
    raise ManifestError(f"{path}: cần `{name_key}` (tên) hoặc `{id_key}`.")


def resolve_refs(spec: dict[str, Any], refs: Refs) -> dict[str, Any]:
    """Spec trong manifest (tham chiếu bằng tên) → dict đúng AgentSpec (UUID)."""
    out = copy.deepcopy(spec)
    for i, raw in enumerate(out.get("connections") or []):
        path = f"spec.connections[{i}]"
        if not isinstance(raw, dict):
            raise ManifestError(f"{path}: phải là object.")
        ref = _ref_of(raw, "connection", "connection_id", path)
        raw["connection_id"] = _lookup("kết nối", path, ref, refs.connections)
    kbs = out.get("knowledge_bases") or []
    out["knowledge_bases"] = [
        _lookup("kho tri thức", f"spec.knowledge_bases[{i}]", kb, refs.knowledge_bases)
        for i, kb in enumerate(kbs)
    ]
    for i, raw in enumerate(out.get("mcp_servers") or []):
        path = f"spec.mcp_servers[{i}]"
        if not isinstance(raw, dict):
            raise ManifestError(f"{path}: phải là object.")
        ref = _ref_of(raw, "server", "server_id", path)
        raw["server_id"] = _lookup("MCP server", path, ref, refs.mcp_servers)
    team = out.get("team")
    if isinstance(team, dict):
        for i, raw in enumerate(team.get("members") or []):
            path = f"spec.team.members[{i}]"
            if not isinstance(raw, dict):
                raise ManifestError(f"{path}: phải là object.")
            ref = _ref_of(raw, "agent", "agent_id", path)
            raw["agent_id"] = _lookup("agent", path, ref, refs.agents)
        team["exclude"] = [
            _lookup("agent", f"spec.team.exclude[{i}]", a, refs.agents)
            for i, a in enumerate(team.get("exclude") or [])
        ]
    return out


def name_refs(spec: dict[str, Any], refs: Refs) -> dict[str, Any]:
    """AgentSpec (json) → spec manifest: UUID đổi thành tên khi biết tên (portable)."""
    out = copy.deepcopy(spec)

    def named(table: dict[UUID, str]) -> Callable[[str], str]:
        def convert(value: str) -> str:
            try:
                return table.get(UUID(str(value)), str(value))
            except ValueError:
                return str(value)

        return convert

    to_conn, to_kb = named(refs.connections), named(refs.knowledge_bases)
    to_mcp, to_agent = named(refs.mcp_servers), named(refs.agents)

    def swap(
        item: dict[str, Any], id_key: str, name_key: str, conv: Callable[[str], str]
    ) -> dict[str, Any]:
        value = item.pop(id_key)
        name = conv(value)
        return {name_key: name, **item} if name != str(value) else {id_key: value, **item}

    if out.get("connections"):
        out["connections"] = [
            swap(c, "connection_id", "connection", to_conn) for c in out["connections"]
        ]
    if out.get("knowledge_bases"):
        out["knowledge_bases"] = [to_kb(k) for k in out["knowledge_bases"]]
    if out.get("mcp_servers"):
        out["mcp_servers"] = [swap(m, "server_id", "server", to_mcp) for m in out["mcp_servers"]]
    team = out.get("team")
    if isinstance(team, dict):
        if team.get("members"):
            team["members"] = [swap(m, "agent_id", "agent", to_agent) for m in team["members"]]
        if team.get("exclude"):
            team["exclude"] = [to_agent(a) for a in team["exclude"]]
    return out


def build_manifest(spec: dict[str, Any], *, agent_id: UUID | None = None) -> dict[str, Any]:
    meta: dict[str, Any] = {"name": spec.get("name", "")}
    if agent_id is not None:
        meta["id"] = str(agent_id)
    return {"apiVersion": API_VERSION, "kind": KIND, "metadata": meta, "spec": spec}


def changed_fields(old: dict[str, Any], new: dict[str, Any]) -> list[str]:
    keys = sorted(set(old) | set(new))
    return [k for k in keys if old.get(k) != new.get(k)]
