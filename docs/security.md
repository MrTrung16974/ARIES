# Bảo mật

Phạm vi: triển khai on-premise, Keycloak làm IdP, mọi token/credential lưu trong hệ thống (OpenBao). Chưa có yêu cầu tuân thủ cụ thể, nhưng các cơ chế audit/retention/xóa dữ liệu được làm sẵn.

## 1. Các lớp phòng thủ

```
[Client] ─TLS─► [Traefik: TLS, rate limit, security headers]
  ─► [AuthN: Keycloak JWT] ─► [AuthZ: RBAC + ABAC (Cedar)]
  ─► [Input guardrail] ─► [Agent runtime]
        └─ mỗi tool call ─► ToolGateway:
              Policy ─► (HITL) ─► Credential Broker ─► Sandbox / Egress proxy
              ─► Output guardrail + DLP ─► Audit
```

## 2. Danh tính & phân quyền

### Keycloak
- Realm `aiagent`; client `aiagent-web` (public, PKCE), `aiagent-api` (bearer-only), `aiagent-worker` (service account).
- MFA (TOTP/WebAuthn) bắt buộc với Admin/Owner; có thể federate LDAP/AD nội bộ.
- Access token 5 phút, refresh token xoay vòng. Angular lưu token trong **sessionStorage** (mất khi đóng tab; không dùng localStorage) — bắt buộc vì luồng PKCE phải giữ `code_verifier` qua lần chuyển hướng tới Keycloak. Rủi ro XSS được giảm bằng CSP chặt (không inline script) và token chỉ gắn vào request tới `/api`.
- API xác thực JWT bằng JWKS của Keycloak (cache, kiểm `iss`, `aud`, `exp`).

### Vai trò

| Role | Quyền |
|---|---|
| Owner | Toàn quyền tenant, quản lý policy, vault, audit |
| Admin | Quản lý user, connector, MCP allowlist, channel |
| Builder | Tạo/sửa agent, KB, skill (trong phạm vi được giao) |
| User | Chat với agent được chia sẻ, tạo connection cá nhân (Gmail, Drive) |
| Viewer | Xem |
| Auditor | Đọc audit log |

### ABAC & cô lập dữ liệu
- Policy viết bằng **Cedar** (`cedarpy`, chạy in-process), version trong git, có unit test.
- `tenant_id` ở mọi bảng + **Postgres Row-Level Security**; session DB set `app.tenant_id`.
- RAG: chunk mang ACL của tài liệu gốc; filter ACL **trong câu query vector**, không lọc sau.
- Connection cá nhân (Gmail, Drive của user A) chỉ dùng được trong run do chính A khởi tạo, trừ khi được chia sẻ ở cấp workspace.

## 3. Secrets & credential

- **OpenBao** tự host: KV v2 cho OAuth token/API key, Transit engine để mã hóa envelope dữ liệu nhạy cảm trong DB.
- Unseal: Shamir key chia cho nhiều người giữ (hoặc auto-unseal bằng HSM nếu có).
- App xác thực với OpenBao bằng AppRole, token ngắn hạn, policy tối thiểu theo service.
- **Credential Broker**: agent/LLM chỉ thấy `connection_id`; token được gắn vào request ở adapter. Token **không bao giờ** xuất hiện trong prompt, log, trace, response API.
- Tự refresh OAuth token; cảnh báo khi refresh thất bại; thu hồi (revoke) khi user ngắt kết nối hoặc bị vô hiệu.
- Rotate định kỳ: client secret Google, Telegram bot token, DB password, AppRole secret.

## 4. Bảo mật LLM (OWASP Top 10 for LLM)

### Risk level của tool

| Level | Ví dụ | Mặc định |
|---|---|---|
| `read` | `gmail.search`, `db.query`, `web.search`, `drive.read` | Tự động |
| `write` | `gmail.create_draft`, `drive.create` | Tự động (cấu hình được) |
| `external_send` | `gmail.send`, `telegram.send_to_other`, HTTP POST | **Cần duyệt** |
| `destructive` | `gmail.delete`, `db.write` | **Cần duyệt**, mặc định tắt |

### Prompt injection & lethal trifecta
- Output của tool được gắn nhãn `untrusted` và bọc delimiter; system prompt nêu rõ không làm theo chỉ dẫn trong dữ liệu.
- **Taint tracking theo run**: khi run đã đọc nội dung untrusted (email, web, file), mọi tool `external_send`/`destructive` bắt buộc HITL — kể cả khi agent cấu hình tự động.
- Nội dung untrusted không thể đổi recipient, mở thêm scope hay tool.
- Classifier phát hiện injection ở input và tool output (cảnh báo + ghi audit).

### Giới hạn
- `max_iterations`, `timeout_s`, token quota theo run/user/tenant, rate limit theo user/agent/channel.
- DLP output: che email/số điện thoại/CCCD/số thẻ/API key theo cấu hình trước khi trả về hoặc gửi ra ngoài.

### Supply chain
- MCP server: chỉ server trong allowlist do Admin duyệt, ghim version + hash image, chạy trong sandbox.
- Plugin bên thứ ba: ký số (sigstore/cosign), cài vào image build, không cài lúc runtime.

## 4b. Phân loại dữ liệu & định tuyến LLM

| Mức | Ví dụ | Được gửi Claude API? |
|---|---|---|
| `public` | Kết quả web search, tài liệu công khai | Có |
| `internal` | Tài liệu nội bộ thông thường, email công việc | Có |
| `confidential` | Hợp đồng, số liệu kinh doanh | Có (mặc định; Owner có thể đổi) |
| `restricted` | Lương, CCCD, dữ liệu khách hàng nhạy cảm, bảng DB được đánh dấu | **Không** — chỉ model tự host |

- Nguồn gắn nhãn: cấu hình connection (cả DB/bảng/cột), KB, thư mục Drive, label Gmail; DLP tự nâng nhãn khi phát hiện dữ liệu nhạy cảm.
- Nhãn lan truyền theo run (giống taint): run đã chạm dữ liệu `restricted` thì mọi lời gọi LLM sau đó đi tới model tự host.
- **Giai đoạn chưa có GPU**: policy chọn một trong hai — `block` (từ chối, báo người dùng) hoặc `redact` (DLP che trường nhạy cảm rồi mới gửi). Mặc định `block`.
- Mọi quyết định định tuyến ghi audit (run, nhãn, provider được chọn).
- API key Anthropic lưu OpenBao; dùng workspace/key riêng cho prod và staging; xem lại điều khoản lưu trữ dữ liệu của gói API đang dùng.

## 5. Sandbox & mạng

- Code interpreter, script skill, MCP stdio: container **gVisor** (runsc), read-only FS, non-root, seccomp, không network trừ khi qua proxy, giới hạn CPU/RAM/thời gian.
- **Egress proxy (Squid)**: chỉ service `worker`/`api` ra internet qua proxy; allowlist domain theo connector:
  - Google: `oauth2.googleapis.com`, `gmail.googleapis.com`, `www.googleapis.com`
  - Telegram: `api.telegram.org`
  - LLM: `api.anthropic.com`
- Chặn SSRF: tool fetch URL không được tới dải IP nội bộ, loopback, link-local, metadata endpoint; resolve DNS rồi kiểm IP trước khi kết nối.
- Phân vùng mạng: DMZ (Traefik public cho webhook/OAuth callback) — App — Data (Postgres, OpenBao, object storage không ra ngoài).

## 6. Upload & ingest

- Giới hạn kích thước, kiểm MIME thật (magic bytes), quét **ClamAV**.
- Parser chạy trong worker cô lập, timeout, không network.
- File lưu object storage S3-compatible, mã hóa at-rest (SSE), truy cập qua presigned URL ngắn hạn.

## 7. Ứng dụng web

- Angular: CSP chặt (không `unsafe-inline` script), render Markdown của LLM qua sanitizer + **DOMPurify**, cấm `bypassSecurityTrust*` với nội dung LLM.
- Security headers: HSTS, X-Content-Type-Options, frame-ancestors, Referrer-Policy.
- Backend: Pydantic validate mọi input, SQLAlchemy bind parameter, CORS allowlist, lỗi không lộ stack trace.

## 8. Audit & giám sát

- Bảng `audit_events` append-only (role DB không có UPDATE/DELETE), hash-chain để phát hiện sửa đổi.
- Ghi: actor, tenant, agent, run, tool, tham số (đã che), kết quả, người duyệt, IP, thời gian.
- Cảnh báo: tăng đột biến tool call, gửi mail hàng loạt, truy vấn DB lớn, policy deny liên tục, đăng nhập bất thường (Keycloak events).
- Retention cấu hình được; export và xóa dữ liệu theo user (sẵn sàng cho Nghị định 13/2023 nếu cần sau).

## 9. Dữ liệu at-rest & backup

- Postgres: TLS nội bộ, disk encryption (LUKS) trên host; cột nhạy cảm mã hóa qua OpenBao Transit.
- Backup mã hóa (pgBackRest), thử restore định kỳ; backup OpenBao snapshot riêng.

## 10. SDLC

- CI: bandit, semgrep, pip-audit, npm audit, gitleaks, trivy (image), import-linter.
- Bộ **red-team prompt injection** chạy tự động (email/trang web/tài liệu độc → kiểm agent không gửi dữ liệu ra ngoài).
- Pentest trước khi mở cho người dùng thật; review bảo mật cho mọi connector mới.
