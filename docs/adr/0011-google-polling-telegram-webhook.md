# ADR-0011: Google dùng polling, Telegram webhook có fallback

- Trạng thái: Accepted

## Quyết định
Gmail trigger dùng History API polling, Drive dùng Changes API polling — không dùng Pub/Sub push để không phải mở endpoint cho Google. OAuth consent screen ở chế độ Internal (Workspace). Telegram dùng webhook qua DMZ có secret token; fallback long polling khi không mở inbound.

## Hệ quả
+ Ít bề mặt tấn công inbound.
- Độ trễ trigger vài phút.
