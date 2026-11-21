"""Use case agent-as-code (ADR-0022): export, apply (dry-run/publish), thư viện mẫu."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import UUID

import yaml
from pydantic import ValidationError as PydanticValidationError

from aiagent.modules.agents.application import AgentService
from aiagent.modules.agents.domain import Agent, AgentSpec, CreatedVia
from aiagent.modules.audit.application import AuditService
from aiagent.modules.audit.domain import Outcome
from aiagent.modules.connections.application import ConnectionService
from aiagent.modules.identity.domain import Principal
from aiagent.modules.knowledge.application import KnowledgeService
from aiagent.modules.manifests.domain import (
    ManifestError,
    Refs,
    Template,
    build_manifest,
    changed_fields,
    dump_manifest,
    load_manifest,
    name_refs,
    resolve_refs,
)
from aiagent.modules.mcp.application import McpService
from aiagent.modules.runtime.application import ModelCatalog
from aiagent.shared_kernel.errors import ConflictError, NotFoundError, ValidationError

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"


@dataclass(frozen=True)
class ApplyResult:
    action: str  # create | update | unchanged
    agent_id: UUID | None
    version: int | None
    changed: list[str] = field(default_factory=list)
    published: bool = False
    dry_run: bool = False


def _spec_errors(exc: PydanticValidationError) -> str:
    parts = []
    for err in exc.errors()[:5]:
        loc = ".".join(str(x) for x in err.get("loc", ()))
        parts.append(f"spec.{loc}: {err.get('msg')}" if loc else str(err.get("msg")))
    return "; ".join(parts)


class ManifestService:
    def __init__(
        self,
        agents: AgentService,
        connections: ConnectionService,
        knowledge: KnowledgeService,
        mcp: McpService,
        models: ModelCatalog,
        audit: AuditService,
        template_dir: Path = TEMPLATE_DIR,
    ) -> None:
        self._agents = agents
        self._connections = connections
        self._knowledge = knowledge
        self._mcp = mcp
        self._models = models
        self._audit = audit
        self._template_dir = template_dir

    async def _refs(self, principal: Principal) -> Refs:
        return Refs(
            connections={c.id: c.name for c in await self._connections.list_visible(principal)},
            knowledge_bases={k.id: k.name for k in await self._knowledge.list_kbs(principal)},
            mcp_servers={m.id: m.name for m in await self._mcp.list_servers(principal)},
            agents={a.id: a.name for a in await self._agents.list_agents(principal)},
        )

    # ------------------------------------------------------------------ export
    async def export(self, principal: Principal, agent_id: UUID, version: int | None = None) -> str:
        agent = await self._agents.get(principal, agent_id)
        if not self._agents.can_edit(principal, agent):
            raise NotFoundError(
                "Không tìm thấy agent."
            )  # không lộ cấu hình cho người chỉ được chat
        ver = await self._agents.version(principal, agent_id, version or agent.latest_version)
        spec = ver.spec.model_dump(mode="json", exclude_defaults=True)
        spec = {"name": ver.spec.name, "model": ver.spec.model.model_dump(mode="json"), **spec}
        return dump_manifest(
            build_manifest(name_refs(spec, await self._refs(principal)), agent_id=agent.id)
        )

    # ------------------------------------------------------------------ apply
    async def _target(self, principal: Principal, meta: dict[str, Any], name: str) -> Agent | None:
        raw_id = meta.get("id")
        if raw_id:
            try:
                agent = await self._agents.get(principal, UUID(str(raw_id)))
            except (ValueError, NotFoundError):
                agent = None
            if agent is not None and self._agents.can_edit(principal, agent):
                return agent
            if agent is not None:
                raise ConflictError("metadata.id trỏ tới agent bạn không có quyền sửa.")
            return None  # id của môi trường khác → tạo mới
        same = [
            a
            for a in await self._agents.list_agents(principal)
            if a.name == name and self._agents.can_edit(principal, a)
        ]
        if len(same) > 1:
            raise ConflictError(f"Có {len(same)} agent tên {name!r} — thêm metadata.id để chọn.")
        return same[0] if same else None

    async def _parse(self, principal: Principal, text: str) -> tuple[dict[str, Any], AgentSpec]:
        try:
            doc = load_manifest(text)
            raw = dict(doc["spec"])
            meta = doc.get("metadata") or {}
            if meta.get("name") and "name" not in raw:
                raw["name"] = meta["name"]
            resolved = resolve_refs(raw, await self._refs(principal))
            spec = AgentSpec.model_validate(resolved)
        except ManifestError as exc:
            raise ValidationError(str(exc)) from None
        except PydanticValidationError as exc:
            raise ValidationError(_spec_errors(exc)) from None
        return meta, spec

    async def apply(
        self, principal: Principal, text: str, *, dry_run: bool = False, publish: bool = False
    ) -> ApplyResult:
        meta, spec = await self._parse(principal, text)
        target = await self._target(principal, meta, spec.name)
        new = spec.model_dump(mode="json")
        if target is None:
            if dry_run:
                return ApplyResult("create", None, None, sorted(new), dry_run=True)
            self._agents.validate(spec)
            agent, version = await self._agents.create(
                principal, spec, via=CreatedVia.CLI, note="apply manifest"
            )
            result = ApplyResult("create", agent.id, version.version)
        else:
            latest = await self._agents.version(principal, target.id, target.latest_version)
            changed = changed_fields(latest.spec.model_dump(mode="json"), new)
            if not changed:
                result = ApplyResult("unchanged", target.id, latest.version, dry_run=dry_run)
                if dry_run or not publish or target.published_version == latest.version:
                    return result
            elif dry_run:
                return ApplyResult("update", target.id, None, changed, dry_run=True)
            else:
                version = await self._agents.update(
                    principal,
                    target.id,
                    spec,
                    expected_version=target.latest_version,
                    via=CreatedVia.CLI,
                    note="apply manifest",
                )
                result = ApplyResult("update", target.id, version.version, changed)
        if publish and result.agent_id is not None and result.version is not None:
            await self._agents.publish(principal, result.agent_id, result.version)
            result = ApplyResult(
                result.action, result.agent_id, result.version, result.changed, published=True
            )
        await self._audit.record_safely(
            tenant_id=principal.tenant_id,
            actor_id=principal.subject,
            action="agent.applied",
            outcome=Outcome.SUCCESS,
            resource_type="agent",
            resource_id=str(result.agent_id),
            details={
                "action": result.action,
                "version": result.version,
                "published": result.published,
                "changed": result.changed[:20],
            },
        )
        return result

    # ------------------------------------------------------------------ mẫu
    def templates(self) -> list[Template]:
        out = []
        for path in sorted(self._template_dir.glob("*.yaml")):
            doc = load_manifest(path.read_text(encoding="utf-8"))
            info = (doc.get("metadata") or {}).get("template") or {}
            out.append(
                Template(
                    id=path.stem,
                    title=str(info.get("title") or doc["spec"].get("name", path.stem)),
                    description=str(info.get("description", "")),
                    category=str(info.get("category", "Khác")),
                    manifest=doc,
                )
            )
        return out

    async def use_template(self, principal: Principal, template_id: str) -> ApplyResult:
        found = next((t for t in self.templates() if t.id == template_id), None)
        if found is None:
            raise NotFoundError("Không tìm thấy mẫu.")
        doc = {**found.manifest, "metadata": {"name": found.manifest["spec"].get("name")}}
        spec = dict(doc["spec"])
        available = await self._models.available()
        wanted = spec.get("model") or {}
        if available and not any(
            m.provider == wanted.get("provider") and m.model == wanted.get("model")
            for m in available
        ):
            spec["model"] = {
                **wanted,
                "provider": available[0].provider,
                "model": available[0].model,
            }
        doc["spec"] = spec
        # Mẫu luôn tạo agent MỚI (nháp) — không ghi đè agent trùng tên.
        text = yaml.safe_dump(doc, allow_unicode=True, sort_keys=False)
        _meta, parsed = await self._parse(principal, text)
        agent, version = await self._agents.create(
            principal, parsed, via=CreatedVia.CLI, note=f"template:{template_id}"
        )
        await self._audit.record_safely(
            tenant_id=principal.tenant_id,
            actor_id=principal.subject,
            action="agent.created_from_template",
            outcome=Outcome.SUCCESS,
            resource_type="agent",
            resource_id=str(agent.id),
            details={"template": template_id},
        )
        return ApplyResult("create", agent.id, version.version)
