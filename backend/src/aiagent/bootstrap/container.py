"""Composition root: nối Port với Adapter. Đây là nơi DUY NHẤT biết lớp cụ thể của hạ tầng."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import cast

import httpx
from redis.asyncio import Redis

from aiagent.adapters.db import Database
from aiagent.bootstrap.plugins import PluginRegistry
from aiagent.bootstrap.settings import Settings
from aiagent.modules.agents.application import ALLOWED_MODELS, AgentService
from aiagent.modules.agents.infrastructure.sql_agent_repository import SqlAgentRepository
from aiagent.modules.audit.application import AuditService
from aiagent.modules.audit.infrastructure.sql_audit_log import SqlAuditLog
from aiagent.modules.builder.application import ArchitectService, CommandService
from aiagent.modules.builder.domain import (
    ArchitectBrainPort,
    Catalog,
    CatalogConnection,
    CatalogSkill,
    CatalogTool,
)
from aiagent.modules.builder.infrastructure.heuristic_brain import HeuristicArchitectBrain
from aiagent.modules.builder.infrastructure.llm_brain import LlmArchitectBrain
from aiagent.modules.builder.infrastructure.sql_session_repository import (
    SqlBuilderSessionRepository,
)
from aiagent.modules.connections.application import ConnectionService
from aiagent.modules.connections.domain import SecretVaultPort
from aiagent.modules.connections.infrastructure.openbao_vault import OpenBaoVault, UnconfiguredVault
from aiagent.modules.connections.infrastructure.sql_connection_repository import (
    SqlConnectionRepository,
)
from aiagent.modules.conversations.application import ConversationService
from aiagent.modules.conversations.infrastructure.sql_conversation_repository import (
    SqlConversationRepository,
)
from aiagent.modules.identity.application import IdentityService
from aiagent.modules.identity.domain import Principal, TokenVerifierPort
from aiagent.modules.identity.infrastructure.jwks_verifier import JwksTokenVerifier
from aiagent.modules.identity.infrastructure.sql_user_repository import SqlUserRepository
from aiagent.modules.runtime.application import (
    ApprovalService,
    ConnectionRef,
    ModelRouter,
    RunAgentService,
    ToolGateway,
)
from aiagent.modules.runtime.domain import ChatModelPort, ModelUnavailableError
from aiagent.modules.runtime.infrastructure.anthropic_model import AnthropicChatModel
from aiagent.modules.runtime.infrastructure.fake_model import FakeChatModel
from aiagent.modules.runtime.infrastructure.react_graph import LangGraphReactLoop
from aiagent.plugin_sdk import ConnectionContext, Plugin, PluginKind, PluginManifest, ToolSpec
from aiagent.shared_kernel.events import EventBusPort, InProcessEventBus

logger = logging.getLogger(__name__)

HealthCheck = Callable[[], Awaitable[bool]]
Closer = Callable[[], Awaitable[None]]


class PluginSkillCatalog:
    """`SkillCatalogPort` trên PluginRegistry: chỉ skill đang hoạt động."""

    def __init__(self, plugins: PluginRegistry) -> None:
        self._plugins = plugins

    def tools_of(self, skill_id: str) -> list[ToolSpec] | None:
        plugin = self._plugins.get(skill_id)
        if plugin is None or plugin.manifest.kind is not PluginKind.SKILL:
            return None
        return list(plugin.manifest.tools)


class PluginConnectorLookup:
    """`ConnectorLookupPort` trên PluginRegistry."""

    def __init__(self, plugins: PluginRegistry) -> None:
        self._plugins = plugins

    def get(self, plugin_id: str) -> Plugin | None:
        return self._plugins.get(plugin_id)

    def connectors(self) -> list[PluginManifest]:
        return [
            m
            for loaded in self._plugins.active()
            if (m := loaded.manifest) is not None and m.kind is PluginKind.CONNECTOR
        ]


class ConnectionCatalogAdapter:
    """`ConnectionCatalogPort` (agents) trên ConnectionService + PluginRegistry."""

    def __init__(self, service: ConnectionService, plugins: PluginRegistry) -> None:
        self._service = service
        self._plugins = plugins

    async def tools_for(self, principal: Principal, connection_id: str) -> list[ToolSpec] | None:
        found = await self._service.usable(principal, [connection_id])
        if not found:
            return None
        plugin = self._plugins.get(found[0].connector_id)
        return list(plugin.manifest.tools) if plugin is not None else None


class ConnectionAccessAdapter:
    """Credential Broker cho runtime (`ConnectionAccessPort`) trên ConnectionService."""

    def __init__(self, service: ConnectionService) -> None:
        self._service = service

    async def usable(self, principal: Principal, connection_ids: list[str]) -> list[ConnectionRef]:
        found = await self._service.usable(principal, connection_ids)
        return [ConnectionRef(str(c.id), c.name, c.connector_id) for c in found]

    async def open(self, principal: Principal, connection_id: str) -> ConnectionContext:
        return await self._service.open(principal, connection_id)


_MODEL_PREFERENCE = ("claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5", "fake-echo")


class PluginCatalogProvider:
    """Catalog cho Architect: skill đang hoạt động, model có provider đã cấu hình và các
    connection người tạo được dùng (ADR-0016). KB: Phase 4.
    """

    def __init__(
        self,
        plugins: PluginRegistry,
        router: ModelRouter,
        connections: ConnectionService | None = None,
    ) -> None:
        self._plugins = plugins
        self._router = router
        self._connections = connections

    async def _connections_for(self, principal: Principal) -> list[CatalogConnection]:
        if self._connections is None:
            return []
        out = []
        for c in await self._connections.list_visible(principal):
            plugin = self._plugins.get(c.connector_id)
            if plugin is None or not self._connections.can_use(principal, c):
                continue
            m = plugin.manifest
            tools = [CatalogTool(t.name, t.description, t.risk.value) for t in m.tools]
            out.append(CatalogConnection(str(c.id), c.name, c.connector_id, m.name, tools))
        return out

    async def catalog_for(self, principal: Principal) -> Catalog:
        skills = [
            CatalogSkill(
                id=m.id,
                name=m.name,
                description=m.description,
                tools=[CatalogTool(t.name, t.description, t.risk.value) for t in m.tools],
            )
            for loaded in self._plugins.active()
            if (m := loaded.manifest) is not None and m.kind is PluginKind.SKILL
        ]
        configured = set(self._router.providers)
        allowed = {m for p, ms in ALLOWED_MODELS.items() if p in configured for m in ms}
        models = [m for m in _MODEL_PREFERENCE if m in allowed]
        return Catalog(
            skills=sorted(skills, key=lambda s: s.id),
            models=models,
            connections=await self._connections_for(principal),
        )


class _UnavailableStructuredModel:
    async def generate_json(self, request: object) -> object:
        raise ModelUnavailableError(
            "Agent Architect cần Claude API key (AIAGENT_ANTHROPIC_API_KEY)."
        )


def build_architect_brain(
    settings: Settings, providers: dict[str, ChatModelPort]
) -> ArchitectBrainPort:
    anthropic = providers.get("anthropic")
    mode = settings.architect_brain
    if mode == "heuristic" or (mode == "auto" and anthropic is None and settings.llm_fake_enabled):
        logger.warning("Agent Architect dùng bộ não heuristic (chỉ cho dev/demo)")
        return HeuristicArchitectBrain()
    model = (
        anthropic if isinstance(anthropic, AnthropicChatModel) else _UnavailableStructuredModel()
    )
    return LlmArchitectBrain(
        model,  # type: ignore[arg-type]
        architect_model=settings.architect_model,
        fast_model=settings.architect_fast_model,
        judge_model=settings.architect_judge_model,
    )


@dataclass
class Container:
    settings: Settings
    token_verifier: TokenVerifierPort
    identity: IdentityService
    audit: AuditService
    plugins: PluginRegistry
    event_bus: EventBusPort
    agents: AgentService
    conversations: ConversationService
    runs: RunAgentService
    approvals: ApprovalService
    connections: ConnectionService
    model_router: ModelRouter
    architect: ArchitectService
    commands: CommandService
    health_checks: dict[str, HealthCheck] = field(default_factory=dict)
    closers: list[Closer] = field(default_factory=list)

    async def start(self) -> None:
        await self.plugins.start()

    async def stop(self) -> None:
        await self.plugins.stop()
        for close in reversed(self.closers):
            try:
                await close()
            except Exception:
                logger.exception("Lỗi khi đóng tài nguyên")

    async def readiness(self, check_timeout: float = 2.0) -> dict[str, bool]:
        async def run(check: HealthCheck) -> bool:
            try:
                return await asyncio.wait_for(check(), check_timeout)
            except Exception:
                return False

        names = list(self.health_checks)
        results = await asyncio.gather(*(run(self.health_checks[n]) for n in names))
        return dict(zip(names, results, strict=True))


def build_model_providers(settings: Settings) -> dict[str, ChatModelPort]:
    providers: dict[str, ChatModelPort] = {}
    if settings.anthropic_api_key is not None and settings.anthropic_api_key.get_secret_value():
        providers["anthropic"] = AnthropicChatModel(
            settings.anthropic_api_key.get_secret_value(),
            base_url=settings.anthropic_base_url,
        )
    else:
        logger.warning("Chưa cấu hình AIAGENT_ANTHROPIC_API_KEY — agent dùng Claude sẽ không chạy")
    if settings.llm_fake_enabled:
        providers["fake"] = FakeChatModel()
    return providers


def build_container(settings: Settings) -> Container:
    db = Database(settings.database_url.get_secret_value(), pool_size=settings.db_pool_size)
    redis = Redis.from_url(settings.redis_url.get_secret_value())
    http = httpx.AsyncClient(follow_redirects=False)

    async def redis_ok() -> bool:
        return bool(await cast("Awaitable[bool]", redis.ping()))

    plugins = PluginRegistry(disabled=settings.plugins_disabled, config=settings.plugin_config)
    plugins.load_all()

    audit = AuditService(SqlAuditLog(db))
    conversations = ConversationService(SqlConversationRepository(db))
    vault: SecretVaultPort = UnconfiguredVault()
    health_checks: dict[str, HealthCheck] = {"database": db.ping, "redis": redis_ok}
    if settings.openbao_token is not None and settings.openbao_token.get_secret_value():
        bao = OpenBaoVault(
            settings.openbao_addr,
            settings.openbao_token.get_secret_value(),
            http,
            mount=settings.openbao_kv_mount,
        )
        vault = bao
        health_checks["vault"] = bao.ping
    else:
        logger.warning("Chưa cấu hình OpenBao — không tạo được kết nối có secret")
    connections = ConnectionService(
        SqlConnectionRepository(db), vault, PluginConnectorLookup(plugins), audit
    )
    agents = AgentService(
        SqlAgentRepository(db),
        PluginSkillCatalog(plugins),
        connections=ConnectionCatalogAdapter(connections, plugins),
    )
    providers = build_model_providers(settings)
    router = ModelRouter(providers)
    runs = RunAgentService(
        agents=agents,
        conversations=conversations,
        gateway=ToolGateway(
            plugins,
            audit,
            timeout_s=settings.tool_timeout_seconds,
            access=ConnectionAccessAdapter(connections),
        ),
        router=router,
        loop=LangGraphReactLoop(),
        audit=audit,
    )
    architect = ArchitectService(
        sessions=SqlBuilderSessionRepository(db),
        brain=build_architect_brain(settings, providers),
        agents=agents,
        runs=runs,
        catalog=PluginCatalogProvider(plugins, router, connections),
        audit=audit,
        cost_model=settings.architect_model,
    )

    return Container(
        settings=settings,
        token_verifier=JwksTokenVerifier(
            issuer=settings.oidc_issuer,
            audience=settings.oidc_audience,
            jwks_url=settings.jwks_url,
            http=http,
            tenant_claim=settings.oidc_tenant_claim,
            leeway_seconds=settings.oidc_leeway_seconds,
        ),
        identity=IdentityService(SqlUserRepository(db)),
        audit=audit,
        plugins=plugins,
        event_bus=InProcessEventBus(),
        agents=agents,
        conversations=conversations,
        runs=runs,
        approvals=ApprovalService(conversations.repo, audit),
        connections=connections,
        model_router=router,
        architect=architect,
        commands=CommandService(architect, agents, audit),
        health_checks=health_checks,
        closers=[db.dispose, redis.aclose, http.aclose],
    )
