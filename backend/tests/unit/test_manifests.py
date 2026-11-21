"""Manifest agent (ADR-0022): mẫu hợp lệ, đổi tham chiếu tên ⇄ id."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from aiagent.modules.agents.domain import AgentSpec
from aiagent.modules.manifests.application import TEMPLATE_DIR
from aiagent.modules.manifests.domain import (
    ManifestError,
    Refs,
    load_manifest,
    name_refs,
    resolve_refs,
)


@pytest.mark.parametrize("path", sorted(TEMPLATE_DIR.glob("*.yaml")), ids=lambda p: p.stem)
def test_every_template_is_a_valid_portable_agent(path: Path) -> None:
    doc = load_manifest(path.read_text(encoding="utf-8"))
    info = doc["metadata"]["template"]
    assert info["title"] and info["description"] and info["category"]
    spec = AgentSpec.model_validate(doc["spec"])
    assert not spec.connections and not spec.knowledge_bases and not spec.mcp_servers


def test_refs_round_trip_and_errors() -> None:
    conn, kb, a1, a2 = uuid4(), uuid4(), uuid4(), uuid4()
    refs = Refs(
        connections={conn: "DB chung"},
        knowledge_bases={kb: "Sổ tay"},
        agents={a1: "IT", a2: "IT"},  # trùng tên
    )
    spec = {
        "connections": [{"connection_id": str(conn), "allowed_tools": ["q"]}],
        "knowledge_bases": [str(kb)],
        "team": {"members": [{"agent_id": str(a1), "when_to_use": "x"}]},
    }
    named = name_refs(spec, refs)
    assert named["connections"] == [{"connection": "DB chung", "allowed_tools": ["q"]}]
    assert named["knowledge_bases"] == ["Sổ tay"]
    # Tên agent trùng → export vẫn ghi tên, nhưng import phải báo lỗi rõ
    with pytest.raises(ManifestError, match="cùng tên"):
        resolve_refs(named, refs)
    named["team"]["members"] = [{"agent_id": str(a1), "when_to_use": "x"}]
    back = resolve_refs(named, refs)
    assert back["connections"][0]["connection_id"] == str(conn)
    assert back["knowledge_bases"] == [str(kb)]
    with pytest.raises(ManifestError, match=r"spec.knowledge_bases\[0\]"):
        resolve_refs({"knowledge_bases": [str(uuid4())]}, refs)
    with pytest.raises(ManifestError, match="cần `connection`"):
        resolve_refs({"connections": [{"allowed_tools": []}]}, refs)
    with pytest.raises(ManifestError, match="quá lớn"):
        load_manifest("x" * (256 * 1024 + 1))


def test_llm_provider_secret_path_is_allowed_by_openbao_policy() -> None:
    """deploy/openbao/init.sh chỉ cấp quyền secret/data/aries/* cho AppRole của ARIES."""
    from aiagent.modules.runtime.application.model_providers import _vault_path

    assert _vault_path("default", "gemini") == "aries/default/llm/gemini"


def test_provider_error_message_is_surfaced() -> None:
    from aiagent.modules.runtime.infrastructure.openai_compat_model import provider_message

    gemini = (
        '[{"error": {"code": 404, "message": "This model models/gemini-2.5-flash-lite is no '
        'longer available to new users.\\n Please update", "status": "NOT_FOUND"}}]'
    )
    assert provider_message(gemini).startswith("This model models/gemini-2.5-flash-lite is no")
    assert "\n" not in provider_message(gemini)
    assert provider_message('{"error": {"message": "model not found"}}') == "model not found"
    assert provider_message('{"error": "boom"}') == "boom"
    assert provider_message("<html>502</html>") == ""
    assert len(provider_message('{"error": {"message": "' + "x" * 999 + '"}}')) == 300
