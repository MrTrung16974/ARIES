# ADR-0010: ToolGateway với risk level, HITL, taint tracking

- Trạng thái: Accepted

## Quyết định
Mọi tool call (native, MCP, OpenAPI) đi qua ToolGateway: Policy → HITL → Credential Broker → Egress/Sandbox → Audit. Tool có risk read|write|external_send|destructive. Khi run đã đọc nội dung untrusted (email, web, file), external_send/destructive bắt buộc duyệt.

## Hệ quả
+ Giảm rủi ro prompt injection gián tiếp và rò rỉ dữ liệu.
- Thêm thao tác duyệt cho người dùng.
