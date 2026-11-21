# ADR-0020: Publish agent ra ngoài — link chia sẻ, widget nhúng, API key, Telegram (Phase 5)

- Trạng thái: Accepted
- Ngày: 2026-11-21

## Bối cảnh
Agent cần dùng được ngoài giao diện quản trị: trang chat công khai, nhúng vào website nội bộ, gọi
từ hệ thống khác qua API, và chat qua Telegram. Người gọi qua các kênh này **không phải tài khoản
Keycloak** nên phải có mô hình quyền riêng, chặt hơn người dùng nội bộ.

## Quyết định

### 1. "Principal kênh"
Mỗi kênh chạy agent bằng một `Principal` có `channel` (ví dụ `share:<id>`, `apikey:<id>`,
`telegram:<id>`), không có vai trò nào, `subject` = `<kênh>:<id>:<người dùng cuối>`:
- chỉ chạy **bản đã publish**;
- **không có người duyệt** → mọi tool cần duyệt (HITL) bị **từ chối** thay vì treo chờ;
- link công khai (ẩn danh): **không** dùng connection và MCP server (tránh lộ hệ thống nội bộ ra
  Internet); chỉ agent `data_classification = public` mới tạo được link công khai;
- API key / Telegram: dùng được connection/KB `workspace` (đúng như người trong tổ chức), không dùng
  được tài nguyên `personal` của ai.

### 2. Bí mật kênh
- API key `ak_<tenant>.<id>.<bí mật>` và token link `<tenant>.<bí mật>`: chỉ lưu **SHA-256**, hiện
  một lần lúc tạo (tạo lại = vô hiệu link cũ). Phần `tenant` nằm trong token để tra cứu dưới RLS.
- Telegram bot token lưu OpenBao; webhook kiểm `X-Telegram-Bot-Api-Secret-Token`.

### 3. Giới hạn
- Rate limit Redis (cửa sổ phút) theo link/visitor và theo API key; giới hạn số tin/ngày mỗi link.
- Kết quả tool (preview) **không** gửi cho người dùng kênh công khai — chỉ tên tool.
- Widget nhúng: trang `/c/<token>` cho phép đặt trong iframe; nếu link có danh sách
  `allowed_origins` thì API từ chối khi trang nhúng không thuộc danh sách (kiểm thêm, không thay
  được CSP vì nginx phục vụ tĩnh).

### 4. Vận hành
- Feedback 👍/👎 + ghi chú trên từng câu trả lời (cả kênh công khai).
- Trang vận hành (Admin): hội thoại mọi kênh (xem có ghi audit), thống kê lượt chạy/token/chi phí
  theo ngày, agent, kênh.
- Langfuse/OpenTelemetry: hoãn — dữ liệu run/usage/audit hiện có đủ cho quy mô < 50 người.

### 5. Telegram (ADR-0011)
Webhook qua DMZ có secret token; không mở được inbound thì worker **long polling** `getUpdates`.
Mỗi chat Telegram = một hội thoại; ra Internet qua Squid (allowlist `api.telegram.org`).
- Subject `telegram:<bot>:<chat_id>`; chỉ chat riêng (tin nhóm bị bỏ qua); `/start`, `/new` mở
  hội thoại mới; agent publish version mới → chat tự sang hội thoại mới.
- Bot ai cũng nhắn được → agent không `public` **bắt buộc** có danh sách `allowed_chat_ids`; người
  lạ nhận mã chat của mình để admin thêm vào.
- Token chỉ ở OpenBao (`aries/<tenant>/channels/telegram/<bot>`); URL Bot API chứa token nên log
  `httpx`/`httpcore` hạ xuống WARNING và lỗi Bot API được báo không kèm URL.
- Update xử lý đúng một lần: khóa Redis theo `update_id` (Telegram gửi lại khi chậm) + offset
  polling ghi trước khi xử lý. Webhook trả 200 ngay, chạy agent ở nền.
- Nhật ký vận hành suy ra kênh từ tiền tố `user_id` (`share:`, `apikey:`, `telegram:`, còn lại =
  ứng dụng).

## Hệ quả
+ Agent dùng được ngoài hệ thống mà không mở rộng quyền: kênh ngoài luôn "yếu" hơn người nội bộ.
- Tool cần duyệt không dùng được qua kênh ngoài (thiết kế có chủ đích).
