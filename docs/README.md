# ARIES — Tài liệu thiết kế

Nền tảng xây dựng và vận hành nhiều AI agent (LangGraph) có RAG, Skill/Connector, cấu hình qua web, triển khai **on-premise**.

## Mục lục

| Tài liệu | Nội dung |
|---|---|
| [architecture.md](architecture.md) | Kiến trúc tổng thể, module, ports & adapters, plugin, runtime, frontend |
| [security.md](security.md) | Mô hình bảo mật, Keycloak, policy, vault, LLM security, sandbox, audit |
| [integrations.md](integrations.md) | Connector framework, MCP, chi tiết Postgres / Gmail / Telegram / Google Drive / Web search |
| [deployment.md](deployment.md) | Hạ tầng on-premise, các service, mạng, backup |
| [roadmap.md](roadmap.md) | Các phase triển khai và tiêu chí hoàn thành |
| [agent-architect.md](agent-architect.md) | Tạo agent bằng ngôn ngữ tự nhiên (Agent Architect) |
| [llm-providers.md](llm-providers.md) | Provider LLM: Claude, Gemini, Groq, OpenRouter, Ollama (local) |
| [adr/](adr/) | Architecture Decision Records |

## Quyết định đã chốt

| # | Chủ đề | Quyết định |
|---|---|---|
| 1 | Triển khai | **On-premise** (Docker Compose → Kubernetes khi cần scale) |
| 2 | Danh tính | **Keycloak** (OIDC), RBAC + ABAC |
| 3 | Tích hợp đầu tiên | **Postgres, Gmail, Telegram, Google Drive, Web search** |
| 4 | Token / credential | **Toàn bộ nằm trong hệ thống** — không dùng aggregator bên ngoài (Composio, Nango…) |
| 5 | Tuân thủ | Chưa có yêu cầu cụ thể — vẫn thiết kế audit log, retention, export/xóa dữ liệu để bổ sung sau |
| 6 | Backend | Python 3.12, FastAPI, LangGraph, SQLAlchemy 2, Alembic |
| 7 | Frontend | Angular (standalone, signals), Nx workspace |
| 8 | Kiến trúc | Modular monolith + Hexagonal + Plugin (entry points) + MCP |
| 9 | Vector store | PostgreSQL + pgvector (sau `VectorStorePort`, đổi được sang Qdrant) |
| 10 | Secret vault | **OpenBao** (fork mã nguồn mở của HashiCorp Vault), tự host |
| 11 | LLM | **Hybrid theo phân loại dữ liệu**: Claude API (Anthropic) cho dữ liệu `public`/`internal`/`confidential`; dữ liệu `restricted` chỉ được xử lý bằng model tự host. **Chưa có GPU** → giai đoạn đầu dữ liệu `restricted` bị chặn hoặc che (DLP) trước khi gửi. Xem [ADR-0012](adr/0012-llm-hybrid-by-data-classification.md) |
| 12 | Model mặc định | `claude-opus-5-5` cho agent; `claude-sonnet-5-5` / `claude-haiku-5-5` chọn được theo agent; Haiku 5.5 cho tác vụ phụ (đặt tiêu đề, phân loại injection, tóm tắt) |
| 13 | Quy mô giai đoạn đầu | Dưới 50 người dùng |

## Mặc định tạm thời (đổi được, chưa chốt)

| Chủ đề | Mặc định | Ghi chú |
|---|---|---|
| Model tự host | **Ollama** (profile `ollama` trong compose); vLLM dùng chung adapter OpenAI-compatible | Xem [llm-providers.md](llm-providers.md) |
| Multi-tenant | Một tổ chức, nhưng **có `tenant_id` + RLS ngay từ đầu** | Rẻ khi làm sớm, rất đắt khi thêm sau |
| UI kit | **Angular Material 3** (MIT) + icon Material Symbols (Apache-2.0) tự host | Xem ADR-0015 — PrimeNG đã chuyển sang giấy phép thương mại |
| Embedding | `bge-m3` self-host **trên CPU** (đa ngôn ngữ, tốt cho tiếng Việt) | Đủ cho < 50 người; ingest chậm hơn, chuyển GPU khi có |
| Web search | **SearXNG** tự host + adapter Tavily/Brave tùy chọn | Phù hợp on-prem, không cần key bên ngoài |
