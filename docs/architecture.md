# Kiến trúc hệ thống

## 1. Nguyên tắc

1. **Domain không phụ thuộc framework** — core không import FastAPI, SQLAlchemy, LangChain.
2. **Mọi thứ có thể thay đổi nằm sau một Port** (LLM, vector, storage, queue, vault, tracing…).
3. **Mở rộng bằng plugin** (Python entry points) — không sửa core.
4. **Config có `schema_version` + migrator** — nâng cấp không vỡ agent cũ.
5. **Module giao tiếp qua contract** (service interface / event), không đọc bảng của nhau.
6. **API có version** (`/api/v1`), chính sách deprecation rõ ràng.
7. **Secure by default** — mọi tool call đi qua policy, credential broker, audit (xem [security.md](security.md)).

## 2. Tổng quan

```
                        ┌─────────── DMZ ───────────┐
Telegram / Google ────► │ Traefik (webhook, OAuth cb)│
                        └─────────────┬─────────────┘
Người dùng (LAN) ─► Angular SPA ─► Traefik ─► FastAPI (api)
                                              │
     ┌──────────────┬──────────────┬──────────┼───────────┬──────────────┐
     ▼              ▼              ▼          ▼           ▼              ▼
  identity       agents         runtime    knowledge   connections   channels/triggers
  (Keycloak)                   (LangGraph)  (RAG)      (OAuth broker)
     │                             │          │           │
     └──── policy ◄────────────────┘          │           └──► OpenBao (vault)
           audit ◄── event bus ──────────────────────────────────────────
                                              │
                         Redis (queue) ──► worker (ingest, sync, trigger)
                                              │
                      PostgreSQL + pgvector, object storage, Egress proxy ─► Internet
```

## 3. Phân lớp trong mỗi module

```
api/            Interface      — router FastAPI, SSE, webhook handler
application/    Application    — use case, command/query, DTO, event handler
domain/         Domain         — entity, value object, domain event, Port (Protocol)
infrastructure/ Infrastructure — adapter: SQLAlchemy repo, LangGraph, HTTP client…

Phụ thuộc: api → application → domain ← infrastructure
```

DI bằng `dependency-injector`; adapter chọn qua settings, ví dụ `VECTOR_STORE=pgvector`.

## 4. Module (bounded context)

| Module | Trách nhiệm |
|---|---|
| `identity` | User, tenant, role, ánh xạ Keycloak, API key |
| `policy` | Cedar policy, risk level, HITL rule, taint tracking |
| `catalog` | LLM provider, model, embedding model |
| `connections` | Connector registry, OAuth broker, credential (qua vault) |
| `skills` | Skill registry, cấu hình skill theo agent |
| `agents` | AgentDefinition, version, publish/rollback |
| `knowledge` | KB, document, ingest, retrieval, ACL |
| `runtime` | Biên dịch & chạy graph, thread, checkpoint, interrupt/resume |
| `channels` | Telegram (sau: Slack, Zalo…) — hội thoại inbound |
| `triggers` | Cron, webhook, polling (Gmail mới, Drive thay đổi) |
| `audit` | Log append-only, cảnh báo |
| `observability` | Trace, usage, cost |
| `shared_kernel` | Base entity, Result, errors, ids, event bus |

**Quy tắc:** chỉ `runtime/infrastructure` được import LangGraph. Ràng buộc import kiểm tra bằng **import-linter** trong CI.

## 5. Ports

```
ChatModelPort, EmbeddingPort, RerankerPort, VectorStorePort,
DocumentLoaderPort, ChunkerPort, FileStoragePort, JobQueuePort,
EventBusPort, TracerPort, SecretVaultPort, PolicyPort,
GraphTemplatePort, NodePort, SkillPort, ConnectorPort, ChannelPort,
TriggerPort, MemoryPort, GuardrailPort, SearchProviderPort
```

## 6. Plugin

Entry-point groups (`pyproject.toml`):

```
aiagent.skills  aiagent.connectors  aiagent.channels  aiagent.triggers
aiagent.llm_providers  aiagent.vector_stores  aiagent.loaders
aiagent.chunkers  aiagent.graph_templates  aiagent.nodes  aiagent.guardrails
```

Manifest của plugin:

```yaml
id: gmail
kind: connector
version: 1.0.0          # semver của plugin
api_version: 1          # Plugin API của core
auth: { type: oauth2, provider: google, scopes: [...] }
config_schema: {...}    # JSON Schema (sinh từ Pydantic) → form Angular
tools:
  - { name: gmail.search, risk: read }
  - { name: gmail.send,   risk: external_send }
triggers: [gmail.new_message]
capabilities: [network:googleapis.com]
ui_hints: { icon: mail, category: communication }
```

- Plugin chỉ được import `aiagent.plugin_sdk` (API ổn định, semver).
- `api_version` không tương thích → plugin bị vô hiệu, có cảnh báo, không crash hệ thống.
- Lifecycle hook: `on_load`, `on_enable`, `health_check`, `on_unload`.
- Ngoài plugin native: **MCP bridge** (server trong allowlist) và **OpenAPI import**.

## 7. Agent runtime

```
AgentDefinition (JSONB, schema_version)
   │ ConfigMigrator
   ▼
AgentSpec (đã validate)
   │ GraphTemplateRegistry (react | plan_execute | supervisor | custom)
   ▼
GraphBuilder ← NodeRegistry (llm, tool, retrieve, router, human_approval, subagent, guardrail)
   ▼
Middleware: tracing → quota → guardrail_in → [graph] → guardrail_out → usage → audit
   ▼
CompiledGraph (cache theo agent_id + version + hash)
```

**Master agent (ADR-0017, Phase 3c):** template `supervisor` — mỗi agent thành viên là một tool
`delegate__<alias>`; mỗi lần giao tạo run con (cùng danh tính người dùng, độ sâu 1), taint/HITL/ngân
sách lan lên run cha.

Mọi tool call trong graph đi qua `ToolGateway`:
`Policy check → (HITL interrupt) → Credential Broker → Sandbox/Egress → Audit`.

**RunEvent contract** (frontend chỉ phụ thuộc vào đây, không phụ thuộc định dạng LangGraph):

```
RunEvent { run_id, seq, type, payload, ts }
type ∈ token | message | tool_start | tool_end | citation |
       approval_required | delegation_start | delegation_end | error | usage | done
```

### AgentDefinition (schema v1)

```yaml
schema_version: 1
id, name, description
model:
  provider_id: anthropic
  model: claude-opus-5-5
  effort: medium            # low | medium | high | xhigh | max (thay cho temperature)
  max_tokens: 16000
  fallback: default         # xử lý stop_reason = refusal
  local_model_id: null      # model tự host dùng khi dữ liệu restricted (khi có GPU)
system_prompt: "..."
graph_type: react
skills:      [ { skill_id, config, allowed_tools: [...] } ]
connections: [ { connection_id, allowed_tools: [...] } ]
knowledge_bases: [ { kb_id, top_k, score_threshold } ]
sub_agents: []
memory: { window: 20, long_term: false }
limits: { max_iterations: 15, timeout_s: 120, max_tokens_per_run: 100000 }
approval: { require_for: [write, destructive, external_send] }
channels: [ { channel_id } ]
```

### LLM adapter & ModelRouter

```
Graph node "llm" ─► ModelRouter ─► ChatModelPort
                      │  input: AgentSpec.model + mức phân loại cao nhất của run (taint)
                      ├─ public/internal/confidential ─► AnthropicAdapter (Claude API, qua egress proxy)
                      └─ restricted ─► LocalAdapter (vLLM, OpenAI-compatible)
                                       chưa có → chặn hoặc che bằng DLP (theo policy)
```

Lưu ý khi viết `AnthropicAdapter` (model Claude hiện tại):
- **Không dùng `temperature`/`top_p`** (bị API từ chối) — điều chỉnh bằng `effort`. Opus 5.5 mặc định effort `medium`, luôn set rõ.
- **Thinking không tắt được** trên Opus 5.5 — muốn nhanh/rẻ thì hạ effort.
- **Không dùng forced `tool_choice` (`any`/`tool`)** — dùng `auto` + `strict: true` hoặc structured output; graph không được dựa vào việc ép gọi tool.
- Luôn kiểm `stop_reason` (`refusal`, `max_tokens`, `pause_turn`) trước khi đọc nội dung; bật fallback phía server.
- **Prompt caching**: giữ system prompt + danh sách tool ổn định và có thứ tự cố định (không chèn thời gian/ID vào đầu prompt) — giảm mạnh chi phí input.
- Trả về thinking block nguyên vẹn trong lịch sử (checkpoint lưu đầy đủ content, không chỉ text).
- Streaming cho mọi request agent; ghi `usage` vào `RunEvent.usage` để tính quota.
- Thông tin model (context window, max output) lấy từ Models API, không hard-code.

## 8. Dữ liệu & versioning

- `agent_versions` immutable; publish/rollback = đổi con trỏ.
- Alembic theo module (mỗi module một schema Postgres).
- Chunk lưu `embedding_model` + `embedding_version`; đổi model → re-index nền + chuyển alias.
- Checkpoint state có version để resume sau nâng cấp.
- Event bus: in-process → Redis Streams → Kafka; dùng **Outbox pattern**.

## 9. Cấu trúc thư mục

```
AIAgent/
├── backend/
│   ├── pyproject.toml
│   ├── src/aiagent/
│   │   ├── main.py
│   │   ├── bootstrap/          # DI, settings, plugin loader
│   │   ├── shared_kernel/
│   │   ├── plugin_sdk/         # API công khai cho plugin
│   │   ├── modules/
│   │   │   ├── identity/ policy/ catalog/ connections/ skills/
│   │   │   ├── agents/ knowledge/ runtime/ channels/ triggers/
│   │   │   └── audit/ observability/
│   │   └── adapters/           # llm/ vector/ storage/ queue/ vault/ tracing/ search/
│   ├── plugins/
│   │   ├── connector_postgres/ connector_gmail/ connector_gdrive/
│   │   ├── channel_telegram/ tool_web_search/ mcp_bridge/
│   ├── migrations/
│   └── tests/ (unit, contract, integration, e2e, redteam)
├── frontend/                   # Nx workspace
│   ├── apps/web/
│   └── libs/
│       ├── core/ api-client/ ui/ dynamic-form/ run-stream/
│       └── features/
│           ├── agents/ chat/ knowledge/ skills/ connections/
│           └── channels/ approvals/ audit/ settings/
├── deploy/                     # compose, keycloak realm, openbao, traefik, squid
└── docs/
```

## 10. Frontend

- Nx module boundaries: feature chỉ phụ thuộc `core`, `ui`, `api-client`, `dynamic-form`, `run-stream`.
- UI kit: Angular Material 3 (MIT) + Material Symbols SVG tự host; component dùng chung trong `packages/ui` (ADR-0015).
- `api-client` sinh từ OpenAPI — không sửa tay.
- Đăng nhập: OIDC Authorization Code + PKCE với Keycloak (`angular-auth-oidc-client`).
- Extension point: `ToolCallRendererRegistry` (skill tự render kết quả), `WidgetRegistry` (field form đặc biệt).
- Runtime config `assets/config.json`, feature flag từ backend.
- Sau này: micro-frontend (Native Federation) nếu cần.

## 11. Chiến lược nâng cấp

| Thay đổi | Cơ chế |
|---|---|
| API | `/api/v1`, `/api/v2` song song, header `Deprecation`, contract test (schemathesis) |
| Config agent | `schema_version` + migrator + fixture test |
| DB | Alembic expand → migrate → contract |
| Embedding | Index theo version + re-index nền |
| LangGraph/LangChain | Chỉ đụng `runtime/infrastructure`, ghim version, golden-conversation test |
| Plugin | `api_version` + semver + health check |
| Tính năng rủi ro | Feature flag |
| Quyết định lớn | ADR trong `docs/adr/` |

## 12. Lộ trình tách service

1. Hiện tại: `api` + `worker`, chung Postgres (schema riêng mỗi module).
2. Tách `knowledge` (ingest/embedding, có thể cần GPU).
3. Tách `runtime` (agent executor, scale ngang).
4. Event bus → Kafka/Redis Streams.
