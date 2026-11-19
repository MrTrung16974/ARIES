# ADR-0008: Mọi token lưu trong hệ thống (OpenBao)

- Trạng thái: Accepted

## Quyết định
Không dùng aggregator (Composio, Nango, Pipedream…). Tự làm OAuth Broker + Credential Broker; token/secret lưu OpenBao KV v2, mã hóa cột nhạy cảm bằng Transit. LLM chỉ thấy connection_id.

## Hệ quả
+ Toàn quyền kiểm soát dữ liệu, không phụ thuộc bên thứ ba.
- Mỗi connector phải tự viết OAuth/refresh/revoke.
