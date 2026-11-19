# Roadmap

Sản phẩm: nền tảng kiểu Dify, **không có workflow canvas** — agent được tạo và sửa bằng ngôn ngữ tự nhiên
(Agent Architect, [agent-architect.md](agent-architect.md)), slash command, và form nâng cao (ADR-0013).
Mỗi phase kết thúc bằng bản chạy được trên môi trường on-prem.

## Phase 0 — Nền móng ✅ (2026-11-18)
- Monorepo `backend/` (Python, uv), `frontend/` (Nx + Angular), `deploy/` (Compose).
- Compose: Traefik, Postgres+pgvector, Redis, Keycloak, OpenBao, api, worker, web.
- `shared_kernel`, DI container, plugin loader, `plugin_sdk` v1, skill mẫu `echo`.
- Keycloak OIDC + PKCE, xác thực JWT, RBAC; `tenant_id` + RLS; audit append-only hash-chain.
- CI: ruff, mypy, import-linter, bandit, pip-audit, pytest, Nx lint/test/build, gitleaks, trivy.

## Phase 1 — Runtime ✅ (2026-11-19, kiểm bằng model giả — chưa chạy với Claude thật vì chưa có API key)
- AgentSpec v2 + version (immutable) + publish/rollback, CRUD qua API (Builder trở lên).
- Runtime (ADR-0014): LangGraph template ReAct, `ChatModelPort` + adapter Claude (Anthropic SDK),
  ModelRouter, `RunEvent` qua SSE.
- Plugin SDK gọi tool; ToolGateway tối thiểu (validate input, chặn `external_send`/`destructive` khi chưa có HITL, audit).
- Conversations: thread, message (lưu nguyên content block), run + usage.
- UI: danh sách agent, form chỉnh tay (Tổng quan / Prompt / JSON), Playground chat stream có hiển thị tool.

**Xong khi:** tạo agent bằng form/API và chat stream được, có tool call và usage.

## Phase 2 — Agent Architect (tạo agent bằng ngôn ngữ tự nhiên)
- Catalog lọc theo quyền; pipeline classify → understand → clarify → map → compose → validate → eval → present.
- Sửa bằng lời (JSON Patch + diff), eval tự sinh + judge, publish/rollback có xác nhận.
- Command bar (Ctrl+K) + slash command; golden dataset 40 mô tả chạy trong CI.

**Xong khi:** gõ mô tả → bản nháp đã chạy thử trong ≤ 90 giây; sửa bằng lời chỉ đổi đúng phần.

## Phase 3 — Skill, Policy, Connector
- Policy (Cedar) + HITL (interrupt/resume, LangGraph checkpointer Postgres) + hộp duyệt.
- Connections + OpenBao + OAuth broker; `/connect`.
- Connector **Postgres** (SQL guard), **Web search** (SearXNG), egress proxy, MCP bridge.

## Phase 4 — Knowledge
- `/kb`, upload, ingest (parse → chunk → bge-m3), hybrid search + rerank, ACL, trích dẫn.
- **Google Drive** data source; chọn object storage S3-compatible.

## Phase 5 — Publish & vận hành
- Web app link, API key, embed widget, **Telegram** channel.
- Log hội thoại, feedback/annotation, chi phí, quota, Langfuse.

## Phase 6 — Tự động hóa & team
- **Gmail** tool + trigger; `/schedule` (cron, mail mới); `/team` (Supervisor), Plan & Execute.
- Taint tracking (lethal trifecta), DLP output.

## Phase 7 — Agent-as-code & mở rộng
- CLI `aiagent` (OAuth Device Flow), import/export YAML, template gallery.
- Red-team prompt injection tự động, sandbox code interpreter (gVisor), Kubernetes.
