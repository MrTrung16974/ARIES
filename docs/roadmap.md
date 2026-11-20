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

## Phase 2 — Agent Architect ✅ (2026-11-19, kiểm bằng bộ não heuristic — golden dataset với Claude chưa chạy vì chưa có API key)
- Catalog lọc theo quyền; pipeline classify → understand → clarify → map → compose → validate → eval → present.
- Sửa bằng lời (JSON Patch + diff), eval tự sinh + judge, publish/rollback có xác nhận.
- Command bar (Ctrl+K) + slash command; golden dataset 40 mô tả chạy trong CI.

**Xong khi:** gõ mô tả → bản nháp đã chạy thử trong ≤ 90 giây; sửa bằng lời chỉ đổi đúng phần.

## Phase 3a — Policy, HITL, Connection, Postgres, Web search ✅
- Policy (risk + taint) + duyệt thao tác (approve/deny → resume run), hộp duyệt (ADR-0016).
- Connections + OpenBao (Credential Broker); connector **Postgres** (SQL guard); skill **web** (SearXNG, fetch chặn SSRF).
- AgentSpec gắn connection; Agent Architect chọn được kết nối.
- Database demo: `docker compose --profile demo run --rm demo-seed` (role `aries_reader` chỉ đọc).

**Hạn chế còn lại:** OpenBao chạy dev mode (RAM — khởi động lại phải nhập lại secret), web.fetch
chưa chặn DNS rebinding → cả hai xử lý ở 3b (storage bền + AppRole, egress proxy).

## Phase 3b — MCP, egress, OAuth
- MCP bridge (allowlist server, sandbox stdio), egress proxy Squid (allowlist domain).
- OAuth broker (Google) + `/connect`; OpenBao AppRole thay token dev, OpenBao storage bền (raft); Cedar nếu cần ABAC.

## Phase 3c — Master agent (giao việc cho các agent khác) ✅ (2026-11-20, kiểm bằng model giả)
Người dùng chỉ cần nói với **ARIES Master**; Master hiểu yêu cầu, chia việc, giao cho agent
chuyên trách rồi tổng hợp kết quả (ADR-0017).
- AgentSpec: `graph_type: supervisor` + `team` (auto | fixed) + `when_to_use` (trường mới có mặc định, vẫn schema v2).
- Delegation = tool `delegate__<alias>`; mỗi lần giao là một **run con** chạy bằng danh tính người
  dùng (không leo thang quyền), độ sâu tối đa 1, gọi song song có giới hạn.
- Taint và HITL lan từ run con lên Master; duyệt ngay trong hội thoại với Master rồi chạy tiếp cả
  hai tầng. Ngân sách token/chi phí/thời gian dùng chung; chỉ giao cho agent có phân loại dữ liệu
  ≤ Master.
- RunEvent `delegation_start/delegation_end` + UI cây việc; ô "Hỏi ARIES" ở trang chủ.
- ARIES Master mặc định cho mỗi tenant (Admin cấu hình allowlist); team supervisor do Builder tạo.
- Architect: viết `when_to_use` cho mỗi agent, tự thêm vào Master khi publish, `/team …`, eval
  định tuyến (tỉ lệ giao đúng agent).

**Còn lại (chuyển sang sau):** lệnh `/team create|add|remove`, eval định tuyến (tỉ lệ giao đúng ≥ 90%)
và kiểm thử giao song song cần Claude thật (model giả chỉ gọi một tool mỗi lượt); allowlist của
ARIES Master hiện chỉnh bằng cách sửa agent Master (chế độ cố định / `exclude`).

**Xong khi:** hỏi Master một yêu cầu cần 2 agent (ví dụ "tra đơn hàng của An Phát rồi tìm tin
tức mới về công ty này") → Master giao song song, hiển thị tiến độ từng agent, tổng hợp một câu
trả lời; thao tác cần duyệt của agent con được duyệt ngay trong hội thoại với Master; tỉ lệ giao
đúng ≥ 90% trên bộ eval định tuyến.

## Phase 4 — Knowledge
- `/kb`, upload, ingest (parse → chunk → bge-m3), hybrid search + rerank, ACL, trích dẫn.
- **Google Drive** data source; chọn object storage S3-compatible.

## Phase 5 — Publish & vận hành
- Web app link, API key, embed widget, **Telegram** channel.
- Log hội thoại, feedback/annotation, chi phí, quota, Langfuse.

## Phase 6 — Tự động hóa
- **Gmail** tool + trigger; `/schedule` (cron, mail mới) — lịch có thể giao cho Master; Plan & Execute nhiều bước dài.
- Taint tracking (lethal trifecta), DLP output.

## Phase 7 — Agent-as-code & mở rộng
- CLI `aiagent` (OAuth Device Flow), import/export YAML, template gallery.
- Red-team prompt injection tự động, sandbox code interpreter (gVisor), Kubernetes.
