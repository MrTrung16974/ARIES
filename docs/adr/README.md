# Architecture Decision Records

| ADR | Tiêu đề |
|---|---|
| [0001](0001-modular-monolith-hexagonal.md) | Modular monolith + Hexagonal |
| [0002](0002-langgraph-isolated-in-runtime.md) | LangGraph chỉ nằm trong runtime |
| [0003](0003-plugin-entry-points-and-mcp.md) | Plugin qua entry points + MCP |
| [0004](0004-versioned-agent-config.md) | AgentDefinition có version |
| [0005](0005-run-event-contract.md) | RunEvent contract |
| [0006](0006-on-premise-deployment.md) | Triển khai on-premise |
| [0007](0007-keycloak-rbac-abac-cedar.md) | Keycloak + Cedar |
| [0008](0008-in-house-credential-vault.md) | Token lưu trong hệ thống (OpenBao) |
| [0009](0009-pgvector.md) | pgvector |
| [0010](0010-tool-gateway-risk-hitl-taint.md) | ToolGateway, HITL, taint |
| [0011](0011-google-polling-telegram-webhook.md) | Google polling, Telegram webhook |
| [0012](0012-llm-hybrid-by-data-classification.md) | LLM hybrid theo phân loại dữ liệu |
| [0013](0013-no-workflow-canvas-nl-builder.md) | Không workflow canvas — tạo agent bằng ngôn ngữ tự nhiên |
| [0014](0014-runtime-langgraph-anthropic-sdk.md) | Runtime: LangGraph + Anthropic SDK |
| [0015](0015-angular-material-ui.md) | Angular Material thay PrimeNG |
| [0016](0016-hitl-policy-connections.md) | HITL, Policy, Connection (Phase 3a) |
| [0017](0017-master-agent-delegation.md) | Master agent giao việc cho các agent khác (Phase 3c) |
| [0018](0018-phase3b-openbao-egress-mcp-oauth.md) | OpenBao bền + AppRole, egress proxy, MCP bridge, OAuth broker (Phase 3b) |
| [0019](0019-knowledge-rag.md) | Kho tri thức (RAG): TEI bge-m3 tự host, pgvector + từ khóa, RRF, ACL (Phase 4) |
| [0020](0020-publish-channels.md) | Publish: link chia sẻ, widget nhúng, API key, Telegram (Phase 5) |

Mẫu ADR mới: Bối cảnh → Quyết định → Phương án khác → Hệ quả.
