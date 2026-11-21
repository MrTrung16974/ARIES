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

## Phase 3b — MCP, egress, OAuth ✅ (2026-11-20, ADR-0018)
- OpenBao server mode + raft (secret không mất khi khởi động lại) + AppRole cho API (không còn
  root token trong API).
- Egress proxy Squid: mạng app/data internal; :3128 allowlist domain, :3129 web.fetch chặn IP nội
  bộ ở tầng proxy (đóng lỗ DNS rebinding).
- MCP bridge: Admin đăng ký server HTTP/stdio (allowlist lệnh), tool tự khám phá, rủi ro theo
  annotation (ghi đè được), output luôn untrusted, đi qua policy/HITL/audit; gắn vào agent.
- OAuth broker: Authorization Code + PKCE, state một lần (Redis), refresh tự động trong Credential
  Broker (plugin chỉ nhận access token); nhà cung cấp Google + SSO nội bộ (Keycloak); `/connect`.
- Cedar: chưa cần (policy code + test đủ).

**Còn lại:** Architect chưa tự chọn MCP server; Google chưa kiểm thật (cần client id/secret);
sandbox stdio mạnh hơn (gVisor/container riêng) ở Phase 7.

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

## Phase 4 — Knowledge (ADR-0019)
- ✅ Kho tri thức: tải tài liệu (PDF, Word, Markdown, HTML, TXT, CSV — nhận diện theo nội dung),
  ingest nền ở worker (parse → chunk theo cấu trúc → bge-m3 qua TEI CPU tự host).
- ✅ Tìm lai vector + từ khóa (bỏ dấu tiếng Việt) → RRF → rerank tùy chọn; tool `kb.search` có
  trích dẫn `[n]`, output không tin cậy (taint).
- ✅ ACL: KB cá nhân/dùng chung, kiểm quyền theo người chạy, chặn KB mật hơn agent; Architect tự chọn
  KB; trang Tri thức + nguồn trích dẫn trong chat.
- Còn lại: **Google Drive** data source (cần OAuth client thật), OCR cho PDF scan, object storage
  S3-compatible (hiện lưu tệp trên volume qua `BlobStorePort`), lệnh `/kb`.

## Phase 5 — Publish & vận hành (ADR-0020)
- ✅ Link chia sẻ `/c/<token>` + widget nhúng `embed.js` (giới hạn origin, rate limit theo khách và
  theo ngày), API key `ak_…` (`POST /api/v1/public/v1/chat`, JSON hoặc SSE), bot **Telegram**
  (polling hoặc webhook, danh sách chat được phép). Kênh ngoài không có người duyệt → tool cần
  duyệt bị từ chối; link công khai không dùng connection/MCP.
- ✅ Feedback 👍/👎 trên câu trả lời; trang **Vận hành**: hội thoại mọi kênh (mở toàn văn có ghi
  audit), tổng hợp feedback, lượt chạy/token/chi phí theo ngày, agent, kênh.
- Còn lại: annotation (sửa câu trả lời mẫu), quota theo phòng ban/tháng, Langfuse/OpenTelemetry.

## Phase 6 — Tự động hóa
- **Gmail** tool + trigger; `/schedule` (cron, mail mới) — lịch có thể giao cho Master; Plan & Execute nhiều bước dài.
- Taint tracking (lethal trifecta), DLP output.

## Phase 7 — Agent-as-code & mở rộng
- CLI `aiagent` (OAuth Device Flow), import/export YAML, template gallery.
- Red-team prompt injection tự động, sandbox code interpreter (gVisor), Kubernetes.
