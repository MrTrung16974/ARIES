# ADR-0003: Plugin qua entry points + MCP

- Trạng thái: Accepted

## Quyết định
Skill/connector/channel/provider là plugin đăng ký qua Python entry points, chỉ dùng plugin_sdk (semver, api_version). Tích hợp bên ngoài thêm bằng MCP bridge (allowlist) và OpenAPI import.

## Hệ quả
+ Thêm tích hợp không sửa core; tận dụng hệ sinh thái MCP.
- Phải giữ plugin_sdk ổn định.
