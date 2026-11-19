# Tích hợp (Connector, Channel, Trigger, Data source)

## 1. Bốn loại

| Loại | Chiều | Ví dụ |
|---|---|---|
| Tool | Agent → ngoài | `gmail.send`, `db.query`, `web.search` |
| Channel | Người dùng ↔ Agent | Telegram bot |
| Trigger | Ngoài → Agent | Mail mới, cron, webhook |
| Data source | Ngoài → KB | Google Drive đồng bộ vào RAG |

## 2. Ba cách cắm — chung một lớp bảo mật

1. **Native plugin** (entry point) — cho 5 tích hợp đầu tiên.
2. **MCP bridge** — server trong allowlist, tool tự đồng bộ vào catalog.
3. **OpenAPI import** — API nội bộ.

Mọi tool call: `Policy → HITL → Credential Broker → Egress proxy → Audit`. Không dùng aggregator bên ngoài; mọi token nằm trong OpenBao.

## 3. Mô hình dữ liệu

```
connectors     (id, kind, version, manifest)               -- từ plugin
connections    (id, tenant_id, connector_id, owner_type: user|workspace,
                owner_id, credential_ref → OpenBao, granted_scopes,
                config, status, expires_at)
agent ↔ connection: allowed_tools (tập con)
```

## 4. OAuth Broker (Google)

- Authorization Code + **PKCE**, `state` ký HMAC + lưu server chống CSRF, `access_type=offline`, `prompt=consent` để lấy refresh token.
- Callback: `https://aiagent.<domain-công-ty>/api/v1/oauth/callback/google`.
  - Google yêu cầu redirect URI là HTTPS với tên miền hợp lệ (không dùng IP). Tên miền **chỉ cần trình duyệt người dùng truy cập được** — có thể là domain nội bộ có TLS hợp lệ.
- **Google Cloud project — cấu hình OAuth consent screen là "Internal"** (nếu công ty dùng Google Workspace): chỉ user trong domain được ủy quyền, **không phải qua Google verification / CASA** cho scope restricted của Gmail. Nếu cần tài khoản Gmail cá nhân ngoài Workspace → phải "External" + verification, cân nhắc kỹ.
- Incremental authorization: chỉ xin scope khi người dùng bật tính năng tương ứng.
- Refresh token lưu OpenBao; revoke khi user ngắt kết nối.

## 5. Postgres connector

| Mục | Thiết kế |
|---|---|
| Auth | Connection string → OpenBao; khuyến nghị **user DB riêng chỉ đọc** cho từng connection (có thể dùng OpenBao Database secrets engine cấp credential động) |
| Tools | `db.list_schemas` (read), `db.describe_table` (read), `db.query` (read), `db.execute` (destructive, mặc định tắt) |
| SQL guard | Parse bằng **sqlglot**: chỉ 1 câu `SELECT`/`WITH`; chặn DDL/DCL/DML, hàm nguy hiểm (`pg_read_file`, `dblink`, `COPY`…); allowlist schema/bảng/cột; tự thêm `LIMIT` (mặc định 200) |
| Thực thi | Transaction `READ ONLY`, `statement_timeout` 15s, giới hạn kích thước kết quả |
| Bảo vệ dữ liệu | Mask cột nhạy cảm theo cấu hình; khuyến nghị dùng VIEW làm semantic layer |
| Ngữ cảnh | Cache schema + mô tả bảng/cột (do Builder viết) để agent viết SQL chính xác |
| Mạng | DB nghiệp vụ truy cập qua mạng nội bộ, không qua egress proxy |

## 6. Gmail connector

| Mục | Thiết kế |
|---|---|
| Scope | `gmail.readonly` (đọc), `gmail.compose` (nháp), `gmail.send` (gửi) — xin dần |
| Tools | `gmail.search` (read), `gmail.read_message` (read), `gmail.read_thread` (read), `gmail.list_labels` (read), `gmail.create_draft` (write), `gmail.send` (**external_send → luôn duyệt**), `gmail.modify_labels` (write) |
| Untrusted | Nội dung email = **untrusted** → bật taint; HTML chuyển sang text, bỏ ảnh/tracking pixel; link không tự mở |
| Gửi mail | Mặc định tạo **draft** → người dùng duyệt nội dung + người nhận trên UI → gửi. Recipient không được lấy tự động từ nội dung email nếu chưa duyệt |
| Trigger | `gmail.new_message`: **polling History API** (không dùng Pub/Sub push vì on-prem không muốn mở endpoint cho Google push), chu kỳ cấu hình (mặc định 2 phút), lưu `historyId` |
| Quota | Tôn trọng quota Gmail API, backoff khi 429 |

## 7. Google Drive (data source + tool)

| Mục | Thiết kế |
|---|---|
| Scope | `drive.readonly` (đồng bộ/đọc); `drive.file` nếu cần tạo file |
| Data source | Chọn folder/shared drive → đồng bộ vào KB; lần đầu full sync, sau đó **Changes API** (`pageToken`) theo chu kỳ |
| Định dạng | Google Docs/Sheets/Slides export (docx/xlsx/pdf hoặc text), PDF, DOCX… → pipeline ingest chung |
| ACL | Lấy permissions của file → map email sang user hệ thống → lưu ACL trên chunk; user chỉ truy xuất được file mình có quyền trên Drive |
| Tools | `drive.search` (read), `drive.read_file` (read), `drive.create_file` (write, tùy chọn) |
| Untrusted | Nội dung file = untrusted (taint) |

## 8. Telegram channel

| Mục | Thiết kế |
|---|---|
| Bot | Mỗi channel = 1 bot token (OpenBao), gắn với 1 agent |
| Nhận tin | **Webhook** qua Traefik ở DMZ (`/api/v1/channels/telegram/{channel_id}/webhook`), kiểm header `X-Telegram-Bot-Api-Secret-Token`; **fallback long polling** (`getUpdates`) nếu không mở inbound được — chỉ cần outbound tới `api.telegram.org` |
| Danh tính | User gõ `/link` → nhận mã một lần trên web → liên kết `telegram_user_id` ↔ user Keycloak. Chưa liên kết → chỉ dùng agent public, không truy cập connection cá nhân |
| Phạm vi | Allowlist chat/group; trong group chỉ trả lời khi được mention hoặc reply |
| Thread | `thread_id = channel_id:chat_id` → checkpoint LangGraph |
| Phản hồi | Stream bằng `editMessageText` theo nhịp; Markdown escape an toàn; chia tin > 4096 ký tự |
| HITL | Nút inline "Duyệt / Từ chối" cho thao tác cần duyệt; chỉ chính user đã liên kết mới bấm được |
| File | Ảnh/tài liệu gửi vào bot → quét ClamAV → có thể đưa vào ngữ cảnh hoặc KB |
| Giới hạn | Rate limit theo chat; tôn trọng giới hạn của Telegram (≈30 msg/s toàn bot) |

## 9. Web search

| Mục | Thiết kế |
|---|---|
| Provider | `SearchProviderPort`: **SearXNG** tự host (mặc định), adapter Tavily / Brave (tùy chọn, key trong OpenBao) |
| Tools | `web.search` (read), `web.fetch` (read) |
| Fetch | Qua egress proxy; chặn SSRF (IP nội bộ, metadata); giới hạn kích thước/timeout; trích nội dung chính (trafilatura) |
| Untrusted | Kết quả web = untrusted (taint) |
| Cấu hình | Domain allow/deny list theo agent, safe search, ngôn ngữ (vi/en) |

## 10. MCP bridge

- Admin thêm server: transport (stdio trong sandbox / streamable HTTP), image + hash, env (secret từ OpenBao), domain egress được phép.
- Tool đồng bộ vào catalog với risk level mặc định `external_send` cho tới khi Admin phân loại lại.
- Mỗi lần gọi qua ToolGateway như tool native.

## 11. Thêm connector mới — checklist

1. Manifest: auth, scopes tối thiểu, tools + risk level, egress domains.
2. Implement `ConnectorPort` trong `plugins/connector_xxx/` chỉ dùng `plugin_sdk`.
3. Đánh dấu output untrusted nếu dữ liệu đến từ bên ngoài.
4. Contract test (mock API) + test red-team cho tool gửi ra ngoài.
5. Review bảo mật → bật bằng feature flag.
