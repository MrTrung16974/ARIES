"""Composition root: nối Port với Adapter. Đây là nơi DUY NHẤT biết lớp cụ thể của hạ tầng."""

from __future__ import annotations

import asyncio
import dataclasses
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import cast
from urllib.parse import urlsplit

import httpx
from pydantic import SecretStr
from redis.asyncio import Redis

from aiagent.adapters.db import Database
from aiagent.bootstrap.plugins import PluginRegistry
from aiagent.bootstrap.settings import Settings
from aiagent.modules.agents.application import ALLOWED_MODELS, AgentService
from aiagent.modules.agents.infrastructure.sql_agent_repository import SqlAgentRepository
from aiagent.modules.audit.application import AuditService
from aiagent.modules.audit.infrastructure.sql_audit_log import SqlAuditLog
from aiagent.modules.automation.application import AutomationService
from aiagent.modules.automation.infrastructure import (
    ArqScheduleQueue,
    PluginTriggerSource,
    RuntimeRunner,
    SqlScheduleRepository,
    SqlTenantList,
)
from aiagent.modules.builder.application import ArchitectService, CommandService
from aiagent.modules.builder.domain import (
    ArchitectBrainPort,
    Catalog,
    CatalogConnection,
    CatalogKnowledgeBase,
    CatalogSkill,
    CatalogTool,
)
from aiagent.modules.builder.infrastructure.heuristic_brain import HeuristicArchitectBrain
from aiagent.modules.builder.infrastructure.llm_brain import LlmArchitectBrain
from aiagent.modules.builder.infrastructure.sql_session_repository import (
    SqlBuilderSessionRepository,
)
from aiagent.modules.channels.application import ChannelService
from aiagent.modules.channels.application.telegram import TelegramService
from aiagent.modules.channels.infrastructure.adapters import (
    RedisRateLimiter,
    SqlChannelRepository,
)
from aiagent.modules.channels.infrastructure.telegram import (
    HttpTelegramClient,
    RedisPollRegistry,
    RuntimeAgentRunner,
    SqlTelegramBotRepository,
)
from aiagent.modules.connections.application import ConnectionService, OAuthBroker
from aiagent.modules.connections.domain import OAuthProvider, SecretVaultPort
from aiagent.modules.connections.infrastructure.oauth_http import (
    HttpOAuthClient,
    RedisOAuthStateStore,
)
from aiagent.modules.connections.infrastructure.openbao_vault import (
    AppRoleCredentials,
    OpenBaoVault,
    UnconfiguredVault,
)
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
from aiagent.modules.knowledge.application import KnowledgeAccess, KnowledgeService
from aiagent.modules.knowledge.infrastructure.adapters import (
    ArqIngestQueue,
    FileBlobStore,
    TeiEmbedder,
    TeiReranker,
)
from aiagent.modules.knowledge.infrastructure.sql_knowledge_repository import (
    SqlKnowledgeRepository,
)
from aiagent.modules.manifests.application import ManifestService
from aiagent.modules.mcp.application import McpService
from aiagent.modules.mcp.infrastructure.sdk_client import SdkMcpClient
from aiagent.modules.mcp.infrastructure.sql_mcp_repository import SqlMcpServerRepository
from aiagent.modules.ops.application import OpsService
from aiagent.modules.ops.infrastructure import SqlOpsQuery
from aiagent.modules.runtime.application import (
    ApprovalService,
    ConnectionRef,
    ModelCatalog,
    ModelProviderService,
    ModelRouter,
    RunAgentService,
    ToolGateway,
)
from aiagent.modules.runtime.domain import ChatModelPort, ModelUnavailableError
from aiagent.modules.runtime.infrastructure.anthropic_model import AnthropicChatModel
from aiagent.modules.runtime.infrastructure.fake_model import FakeChatModel
from aiagent.modules.runtime.infrastructure.openai_compat_model import (
    OpenAICompatChatModel,
    OpenAICompatConfig,
)
from aiagent.modules.runtime.infrastructure.provider_factory import DefaultProviderFactory
from aiagent.modules.runtime.infrastructure.react_graph import LangGraphReactLoop
from aiagent.modules.runtime.infrastructure.sql_provider_repository import SqlProviderRepository
from aiagent.plugin_sdk import (
    ConnectionContext,
    NetworkPolicy,
    Plugin,
    PluginKind,
    PluginManifest,
    ToolSpec,
)
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
        if not found or self._plugins.get(found[0].connector_id) is None:
            return None
        return self._service.tools_of(found[0])


class ConnectionAccessAdapter:
    """Credential Broker cho runtime (`ConnectionAccessPort`) trên ConnectionService."""

    def __init__(self, service: ConnectionService) -> None:
        self._service = service

    async def usable(self, principal: Principal, connection_ids: list[str]) -> list[ConnectionRef]:
        found = await self._service.usable(principal, connection_ids)
        return [
            ConnectionRef(str(c.id), c.name, c.connector_id, tuple(self._service.tools_of(c)))
            for c in found
        ]

    async def open(self, principal: Principal, connection_id: str) -> ConnectionContext:
        return await self._service.open(principal, connection_id)


class PluginCatalogProvider:
    """Catalog cho Architect: skill đang hoạt động, model có provider đã cấu hình và các
    connection người tạo được dùng (ADR-0016). KB: Phase 4.
    """

    def __init__(
        self,
        plugins: PluginRegistry,
        models: ModelCatalog,
        connections: ConnectionService | None = None,
        knowledge: KnowledgeService | None = None,
    ) -> None:
        self._plugins = plugins
        self._models = models
        self._connections = connections
        self._knowledge = knowledge

    async def _knowledge_for(self, principal: Principal) -> list[CatalogKnowledgeBase]:
        if self._knowledge is None:
            return []
        return [
            CatalogKnowledgeBase(str(k.id), k.name, k.description, k.doc_count)
            for k in await self._knowledge.list_kbs(principal)
            if self._knowledge.can_read(principal, k)
        ]

    async def _connections_for(self, principal: Principal) -> list[CatalogConnection]:
        if self._connections is None:
            return []
        out = []
        for c in await self._connections.list_visible(principal):
            plugin = self._plugins.get(c.connector_id)
            if plugin is None or not self._connections.can_use(principal, c):
                continue
            tools = [
                CatalogTool(t.name, t.description, t.risk.value)
                for t in self._connections.tools_of(c)
            ]
            out.append(
                CatalogConnection(str(c.id), c.name, c.connector_id, plugin.manifest.name, tools)
            )
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
        entries = await self._models.available()
        model_providers: dict[str, str] = {}
        for e in entries:
            model_providers.setdefault(e.model, e.provider)
        return Catalog(
            skills=sorted(skills, key=lambda s: s.id),
            models=list(model_providers),
            model_providers=model_providers,
            connections=await self._connections_for(principal),
            knowledge_bases=await self._knowledge_for(principal),
        )


class _UnavailableStructuredModel:
    async def generate_json(self, request: object) -> object:
        raise ModelUnavailableError(
            "Agent Architect chưa có model: cấu hình AIAGENT_ARCHITECT_PROVIDER và API key tương ứng."
        )


def build_architect_brain(
    settings: Settings, providers: dict[str, ChatModelPort]
) -> ArchitectBrainPort:
    chosen = providers.get(settings.architect_provider)
    structured = isinstance(chosen, AnthropicChatModel | OpenAICompatChatModel)
    mode = settings.architect_brain
    if mode == "heuristic" or (mode == "auto" and not structured and settings.llm_fake_enabled):
        logger.warning("Agent Architect dùng bộ não heuristic (chỉ cho dev/demo)")
        return HeuristicArchitectBrain()
    model = chosen if structured else _UnavailableStructuredModel()
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
    mcp: McpService
    oauth: OAuthBroker
    knowledge: KnowledgeService
    channels: ChannelService
    ops: OpsService
    telegram: TelegramService
    automation: AutomationService
    manifests: ManifestService
    model_router: ModelRouter
    model_catalog: ModelCatalog
    model_providers: ModelProviderService
    architect: ArchitectService
    commands: CommandService
    health_checks: dict[str, HealthCheck] = field(default_factory=dict)
    closers: list[Closer] = field(default_factory=list)

    async def start(self) -> None:
        await self.plugins.start()
        await self.model_providers.hydrate_all()

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


# Tên dịch vụ hạ tầng trong deploy/docker-compose.yml — connector không được trỏ vào.
_INFRA_HOSTS = (
    "traefik",
    "postgres",
    "redis",
    "openbao",
    "searxng",
    "squid",
    "ollama",
    "embedder",
    "reranker",
    "keycloak",
    "api",
    "worker",
    "web",
)


def build_network_policy(settings: Settings) -> NetworkPolicy:
    """Chính sách đích mạng cho connector (ADR-0023): chặn hạ tầng của chính ARIES."""
    urls = [
        settings.database_url.get_secret_value(),
        settings.redis_url.get_secret_value(),
        settings.openbao_addr,
        settings.embedder_url,
        settings.reranker_url,
        settings.oidc_issuer,
        settings.ollama_base_url,
    ]
    if settings.migrator_database_url is not None:
        urls.append(settings.migrator_database_url.get_secret_value())
    urls += [str(v) for cfg in settings.plugin_config.values() for v in cfg.values()]
    hosts = set(_INFRA_HOSTS) | set(settings.connector_blocked_hosts)
    for url in urls:
        if url and "://" in str(url):
            try:
                host = urlsplit(str(url)).hostname
            except ValueError:
                continue
            if host:
                hosts.add(host)
    allowed = {h.strip().lower() for h in settings.connector_allowed_internal_hosts}
    return NetworkPolicy.build(
        blocked_hosts=sorted(h for h in hosts if h.lower() not in allowed),
        allowed_networks=settings.connector_allowed_networks,
        allow_loopback=settings.connector_allow_loopback,
    )


def build_oauth_providers(settings: Settings) -> dict[str, OAuthProvider]:
    return {
        pid: OAuthProvider(
            id=pid,
            name=p.name,
            authorize_url=p.authorize_url,
            token_url=p.token_url,
            userinfo_url=p.userinfo_url,
            client_id=p.client_id,
            client_secret=p.client_secret.get_secret_value(),
            scopes=tuple(p.scopes),
            extra_params=dict(p.extra_params),
        )
        for pid, p in settings.all_oauth_providers().items()
    }


def _secret(value: SecretStr | None) -> str:
    return value.get_secret_value() if value is not None else ""


def openai_compat_configs(settings: Settings) -> list[tuple[OpenAICompatConfig, list[str]]]:
    """Provider tương thích OpenAI đã cấu hình → (config, danh sách model tĩnh; rỗng = tự lấy)."""
    out: list[tuple[OpenAICompatConfig, list[str]]] = []
    if key := _secret(settings.gemini_api_key):
        out.append(
            (
                OpenAICompatConfig(
                    "gemini", settings.gemini_base_url, key, "Gemini", send_reasoning_effort=True
                ),
                settings.gemini_models,
            )
        )
    if key := _secret(settings.groq_api_key):
        out.append(
            (OpenAICompatConfig("groq", settings.groq_base_url, key, "Groq"), settings.groq_models)
        )
    if key := _secret(settings.openrouter_api_key):
        out.append(
            (
                OpenAICompatConfig(
                    "openrouter",
                    settings.openrouter_base_url,
                    key,
                    "OpenRouter",
                    model_suffix=":free" if settings.openrouter_free_only else None,
                    extra_headers={"X-Title": settings.app_name},
                ),
                settings.openrouter_models,
            )
        )
    if settings.ollama_base_url:
        base = settings.ollama_base_url.rstrip("/")
        out.append(
            (
                OpenAICompatConfig(
                    "ollama", base if base.endswith("/v1") else f"{base}/v1", None, "Ollama"
                ),
                settings.ollama_models,
            )
        )
    return out


def fallback_models(settings: Settings) -> dict[str, list[str]]:
    """Model dự phòng theo provider (dùng khi model chính quá tải / bị giới hạn)."""
    return {
        "gemini": settings.gemini_fallback_models,
        "groq": settings.groq_fallback_models,
        "openrouter": settings.openrouter_fallback_models,
        "ollama": settings.ollama_fallback_models,
    }


def build_model_providers(settings: Settings) -> dict[str, ChatModelPort]:
    providers: dict[str, ChatModelPort] = {}
    if key := _secret(settings.anthropic_api_key):
        providers["anthropic"] = AnthropicChatModel(key, base_url=settings.anthropic_base_url)
    fallbacks = fallback_models(settings)
    for cfg, _ in openai_compat_configs(settings):
        cfg = dataclasses.replace(
            cfg,
            fallback_models=tuple(fallbacks.get(cfg.provider, [])),
            overload_retries=settings.llm_overload_retries,
            retry_delay_s=settings.llm_retry_delay_seconds,
        )
        providers[cfg.provider] = OpenAICompatChatModel(
            cfg, timeout_s=settings.llm_request_timeout_seconds
        )
    if not providers:
        logger.warning(
            "Chưa cấu hình provider LLM nào (Anthropic/Gemini/Groq/OpenRouter/Ollama) — "
            "chỉ chạy được model giả nếu bật"
        )
    if settings.llm_fake_enabled:
        providers["fake"] = FakeChatModel()
    return providers


def build_allowed_models(settings: Settings) -> dict[str, frozenset[str] | None]:
    allowed: dict[str, frozenset[str] | None] = dict(ALLOWED_MODELS)
    for cfg, models in openai_compat_configs(settings):
        allowed[cfg.provider] = frozenset(models) if models else None
    return allowed


def build_container(settings: Settings) -> Container:
    db = Database(settings.database_url.get_secret_value(), pool_size=settings.db_pool_size)
    redis = Redis.from_url(settings.redis_url.get_secret_value())
    # Client cho dịch vụ NỘI BỘ (Keycloak JWKS, OpenBao): không đi qua egress proxy.
    http = httpx.AsyncClient(follow_redirects=False, trust_env=False)

    async def redis_ok() -> bool:
        return bool(await cast("Awaitable[bool]", redis.ping()))

    plugins = PluginRegistry(disabled=settings.plugins_disabled, config=settings.plugin_config)
    plugins.load_all()

    audit = AuditService(SqlAuditLog(db))
    conversations = ConversationService(SqlConversationRepository(db))
    vault: SecretVaultPort = UnconfiguredVault()
    health_checks: dict[str, HealthCheck] = {"database": db.ping, "redis": redis_ok}
    token = settings.openbao_token.get_secret_value() if settings.openbao_token else ""
    approle = (
        AppRoleCredentials(settings.openbao_role_id_file, settings.openbao_secret_id_file)
        if settings.openbao_role_id_file and settings.openbao_secret_id_file
        else None
    )
    if token or approle is not None:
        bao = OpenBaoVault(
            settings.openbao_addr,
            http,
            token=token or None,
            approle=approle,
            mount=settings.openbao_kv_mount,
        )
        vault = bao
        health_checks["vault"] = bao.ping
    else:
        logger.warning("Chưa cấu hình OpenBao — không tạo được kết nối có secret")
    # Client ra Internet (OAuth token endpoint…): tôn trọng HTTPS_PROXY → đi qua egress proxy.
    egress_http = httpx.AsyncClient(follow_redirects=False)
    oauth_client = HttpOAuthClient(egress_http)
    connections = ConnectionService(
        SqlConnectionRepository(db),
        vault,
        PluginConnectorLookup(plugins),
        audit,
        oauth=oauth_client,
        providers=build_oauth_providers(settings),
        network=build_network_policy(settings),
    )
    oauth = OAuthBroker(
        connections,
        RedisOAuthStateStore(redis),
        oauth_client,
        redirect_uri=f"{settings.public_base_url.rstrip('/')}/api/v1/oauth/callback",
    )
    mcp = McpService(
        SqlMcpServerRepository(db),
        vault,
        SdkMcpClient(timeout_s=settings.tool_timeout_seconds),
        audit,
        settings.mcp_stdio_commands,
    )
    ingest_queue = ArqIngestQueue(settings.redis_url.get_secret_value())
    schedule_queue = ArqScheduleQueue(settings.redis_url.get_secret_value())
    embedder = TeiEmbedder(settings.embedder_url, http)
    knowledge = KnowledgeService(
        SqlKnowledgeRepository(db),
        FileBlobStore(settings.kb_storage_path),
        embedder,
        TeiReranker(settings.reranker_url, http) if settings.reranker_url else None,
        ingest_queue,
        audit,
    )
    knowledge_access = KnowledgeAccess(knowledge)

    async def embedder_ok() -> bool:
        r = await http.get(f"{settings.embedder_url.rstrip('/')}/health", timeout=3.0)
        return r.status_code == 200

    health_checks["embedder"] = embedder_ok
    allowed_models = build_allowed_models(settings)
    providers = build_model_providers(settings)
    router = ModelRouter(providers)
    model_catalog = ModelCatalog(allowed_models, providers)
    env_base_urls: dict[str, str] = {
        "gemini": settings.gemini_base_url,
        "groq": settings.groq_base_url,
        "openrouter": settings.openrouter_base_url,
    }
    if settings.anthropic_base_url:
        env_base_urls["anthropic"] = settings.anthropic_base_url
    if settings.ollama_base_url:
        env_base_urls["ollama"] = settings.ollama_base_url
    env_api_keys: dict[str, str] = {}
    for pid, sec in (
        ("anthropic", settings.anthropic_api_key),
        ("gemini", settings.gemini_api_key),
        ("groq", settings.groq_api_key),
        ("openrouter", settings.openrouter_api_key),
    ):
        if val := _secret(sec):
            env_api_keys[pid] = val
    model_providers = ModelProviderService(
        SqlProviderRepository(db),
        vault,
        DefaultProviderFactory(
            app_name=settings.app_name,
            timeout_s=settings.llm_request_timeout_seconds,
            fallback_models=fallback_models(settings),
            overload_retries=settings.llm_overload_retries,
            retry_delay_s=settings.llm_retry_delay_seconds,
        ),
        router,
        model_catalog,
        audit,
        env_providers=dict(providers),
        env_allowed=dict(allowed_models),
        env_base_urls=env_base_urls,
        env_api_keys=env_api_keys,
        list_tenants=SqlTenantList(db).tenant_ids,
    )
    agents = AgentService(
        SqlAgentRepository(db),
        PluginSkillCatalog(plugins),
        allowed_models=model_catalog.allowed,
        connections=ConnectionCatalogAdapter(connections, plugins),
        mcp=mcp,
        knowledge=knowledge_access,
    )
    runs = RunAgentService(
        agents=agents,
        conversations=conversations,
        gateway=ToolGateway(
            plugins,
            audit,
            timeout_s=settings.tool_timeout_seconds,
            access=ConnectionAccessAdapter(connections),
            mcp=mcp,
            knowledge=knowledge_access,
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
        catalog=PluginCatalogProvider(plugins, model_catalog, connections, knowledge),
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
        mcp=mcp,
        oauth=oauth,
        knowledge=knowledge,
        channels=ChannelService(SqlChannelRepository(db), agents, RedisRateLimiter(redis), audit),
        ops=OpsService(SqlOpsQuery(db), conversations.repo, agents, audit),
        telegram=TelegramService(
            SqlTelegramBotRepository(db),
            agents,
            conversations.repo,
            RuntimeAgentRunner(runs),
            HttpTelegramClient(egress_http),
            vault,
            RedisPollRegistry(redis),
            RedisRateLimiter(redis),
            audit,
            settings.public_base_url,
        ),
        manifests=ManifestService(agents, connections, knowledge, mcp, model_catalog, audit),
        automation=AutomationService(
            SqlScheduleRepository(db),
            agents,
            connections,
            RuntimeRunner(runs),
            PluginTriggerSource(connections, plugins),
            schedule_queue,
            SqlTenantList(db),
            audit,
        ),
        model_router=router,
        model_catalog=model_catalog,
        model_providers=model_providers,
        architect=architect,
        commands=CommandService(architect, agents, audit),
        health_checks=health_checks,
        closers=[
            ingest_queue.close,
            schedule_queue.close,
            db.dispose,
            redis.aclose,
            http.aclose,
            egress_http.aclose,
            *(p.aclose for p in providers.values() if isinstance(p, OpenAICompatChatModel)),
        ],
    )
