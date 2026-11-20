# ADR-0018: OpenBao bền + AppRole, egress proxy, MCP bridge, OAuth broker (Phase 3b)

- Trạng thái: Accepted
- Ngày: 2026-11-20

## 1. OpenBao: lưu bền + AppRole
- OpenBao chạy **server mode, storage `raft`** trên volume (thay dev mode lưu RAM: khởi động lại
  mất secret).
- Service một lần `openbao-init` (chạy mỗi lần `compose up`):
  - lần đầu: `operator init` (dev/staging: 1 key share), bật KV v2 `secret/`, AppRole, policy
    `aries-api` (chỉ `secret/data|metadata/aries/*`), tạo role `aries-api`;
  - mọi lần: unseal, cấp **secret_id mới** (TTL ngắn) cho API vào volume chỉ API đọc được.
- API đăng nhập AppRole (`role_id` + `secret_id` từ file) lấy token có TTL, tự gia hạn/đăng nhập
  lại khi hết hạn hoặc bị 403. Root token chỉ dùng trong `openbao-init`, không vào API.
- Unseal key nằm trên volume của `openbao-init` — **chỉ chấp nhận cho dev/staging**. Prod: Shamir
  nhiều người giữ hoặc auto-unseal (Transit/KMS/HSM), ghi trong docs/deployment.md.

## 2. Egress proxy (Squid) + cô lập mạng
- Mạng `app` và `data` đều **internal** (không ra Internet). Chỉ `traefik`, `keycloak` (cổng cho
  trình duyệt), `squid`, `searxng` nằm thêm trên mạng `egress` có Internet.
- API/worker ra ngoài **chỉ qua Squid**:
  - cổng `3128` (hệ thống): **allowlist domain** — Anthropic API, Google OAuth/API, MCP server
    HTTP được Admin duyệt… (`deploy/squid/allowed-domains.txt`);
  - cổng `3129` (web.fetch): mọi domain công khai, **chặn mọi IP nội bộ/riêng ở tầng proxy** (đóng
    lỗ DNS rebinding của kiểm tra SSRF ở tầng ứng dụng).
- Chỉ HTTP(S) cổng 80/443; CONNECT chỉ 443.

## 3. MCP bridge
- Admin đăng ký **MCP server** (allowlist): `streamable_http` (URL; host phải nằm trong allowlist
  egress) hoặc `stdio` (lệnh nằm trong `AIAGENT_MCP_STDIO_COMMANDS` do người vận hành cài sẵn —
  không cho nhập lệnh tùy ý qua UI).
- Header bí mật (API key) của server lưu OpenBao như connection.
- Tool của server được khám phá (`tools/list`), mỗi tool thành tool `mcp__<server>__<tool>`:
  - risk theo annotation (`readOnlyHint` → read, `destructiveHint` → destructive, còn lại write;
    Admin ghi đè được); output luôn `untrusted` (taint);
  - đi qua ToolGateway như mọi tool: policy, HITL, audit, timeout, cắt kết quả.
- Agent gắn MCP server như gắn skill (`mcp_servers: [{server_id, allowed_tools}]`).
- stdio sandbox: tiến trình con không có secret của ARIES (env tối thiểu + header riêng của
  server), thư mục tạm riêng, timeout, ra Internet qua proxy. Cách ly mạnh hơn (gVisor/container
  riêng) ở Phase 7.

## 4. OAuth broker
- Authorization Code + **PKCE**, `state` một lần (lưu Postgres, TTL 10 phút), redirect về API
  `/api/v1/oauth/callback`. Provider cấu hình qua env (client secret trong OpenBao hoặc env).
- Connector khai báo `auth: oauth` + provider + scope; tạo connection bằng nút "Kết nối với …"
  hoặc lệnh `/connect <provider>`; refresh token lưu OpenBao, Credential Broker đổi access token
  khi gọi tool (cache trong RAM tới gần hết hạn).
- Provider có sẵn: `google`; `keycloak` (dev — để kiểm thử luồng đầy đủ không cần Google).

## 5. Cedar
Chưa dùng: policy hiện tại (risk + taint + vai trò) diễn đạt đủ bằng code và có test. Xét lại khi
có yêu cầu ABAC theo thuộc tính dữ liệu/phòng ban.

## Hệ quả
+ Secret không mất khi khởi động lại; API không giữ root token.
+ API không thể tự ra Internet ngoài allowlist; prompt injection khó exfiltrate dữ liệu.
+ Mở rộng tích hợp qua MCP không cần viết plugin.
- Thêm vận hành: unseal OpenBao, duy trì allowlist domain, cập nhật danh sách lệnh MCP stdio.
