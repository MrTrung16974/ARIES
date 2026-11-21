# ADR-0022: Agent-as-code — manifest YAML, thư viện mẫu, CLI `aiagent` (Phase 7)

- Trạng thái: Accepted
- Ngày: 2026-11-21

## Bối cảnh
Builder muốn quản lý agent như mã nguồn: lưu cấu hình trong git, review thay đổi, đưa từ môi
trường thử sang môi trường thật, và tạo nhanh agent từ mẫu. AgentSpec hiện tham chiếu connection,
KB, MCP server, agent con bằng UUID — không mang sang tenant/môi trường khác được.

## Quyết định

### 1. Manifest
```yaml
apiVersion: aries.ai/v1
kind: Agent
metadata:
  name: Trợ lý nhân sự        # = spec.name
  id: 7f…                     # tùy chọn (có khi export) — apply cập nhật đúng agent này
spec:                          # AgentSpec, nhưng tham chiếu bằng TÊN
  connections: [{connection: "DB nhân sự", allowed_tools: []}]
  knowledge_bases: ["Sổ tay nhân sự"]
  mcp_servers: [{server: "Jira", allowed_tools: []}]
  team: {members: [{agent: "Trợ lý IT", when_to_use: "…"}], exclude: ["…"]}
```
- Export (`GET /agents/{id}/export`, cần quyền sửa) đổi UUID → tên; import nhận **cả** tên lẫn UUID
  (`connection_id`, `server_id`, `agent_id`). Tên không tìm thấy / trùng → lỗi 422 chỉ rõ đường dẫn.
- Chỉ `yaml.safe_load` (không tag Python), tối đa 256 KB, phải là mapping; JSON cũng hợp lệ.

### 2. Apply (`POST /agents/apply`)
- Đích: `metadata.id` (nếu tồn tại và sửa được) → cập nhật; không có id → agent **cùng tên** mà
  người gọi sửa được (đúng 1 → cập nhật, nhiều → 409); không có → tạo mới (`created_via=cli`).
- Spec giống hệt version mới nhất → `unchanged` (không tạo version rỗng).
- `dry_run` trả kế hoạch (`create | update | unchanged`, các trường thay đổi) mà không ghi gì.
- `publish: true` publish luôn version vừa tạo. Mọi kiểm tra quyền connection/KB/MCP/team như tạo
  agent qua form (không có đường tắt).

### 3. Thư viện mẫu
- Mẫu là manifest YAML đóng gói trong backend (`aiagent/templates/*.yaml`) + `metadata.template`
  (tiêu đề, mô tả, nhóm). Mẫu **không** tham chiếu connection/KB (khác nhau mỗi nơi) — chỉ skill.
- `POST /templates/{id}/use` tạo agent nháp từ mẫu; model đổi sang model đầu tiên đang khả dụng nếu
  provider của mẫu chưa cấu hình.

### 4. CLI `aiagent`
- Package `aiagent_cli` (chỉ dùng httpx, PyYAML, stdlib — không import core; có contract riêng).
- Đăng nhập **OAuth 2.0 Device Authorization Grant** (RFC 8628) với client công khai `aiagent-cli`
  của Keycloak: CLI in mã + link, người dùng xác nhận trên trình duyệt (vẫn qua MFA/SSO như web);
  không bao giờ nhập mật khẩu vào CLI. `GET /api/v1/cli/config` cho CLI biết issuer/client.
- Token lưu `~/.config/aries/credentials.json` quyền 0600, tự làm mới bằng refresh token; `logout`
  xóa file và thu hồi refresh token.
- Lệnh: `login`, `logout`, `whoami`, `agents list|export`, `apply -f [--dry-run] [--publish]`,
  `templates list|use`, `chat`.

### 5. Hoãn (Phase 7b)
Red-team prompt injection tự động, sandbox code interpreter (gVisor), triển khai Kubernetes.

## Hệ quả
+ Agent review được qua git/PR, đưa giữa môi trường bằng tên thay vì UUID.
+ CLI không giữ mật khẩu; token gắn người dùng thật, mọi thao tác vẫn qua quyền/audit như web.
- Đổi tên connection/KB làm manifest cũ không apply được (báo lỗi rõ, không âm thầm bỏ qua).
