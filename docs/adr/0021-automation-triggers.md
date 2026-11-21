# ADR-0021: Tự động hóa — lịch chạy, trigger Gmail, taint/DLP (Phase 6)

- Trạng thái: Accepted
- Ngày: 2026-11-21

## Bối cảnh
Người dùng muốn agent tự chạy: "8h sáng thứ Hai tổng hợp…", "có mail mới thì phân loại và soạn
nháp trả lời". Chạy không có người ngồi trước màn hình làm tăng rủi ro prompt injection (nội dung
email do người ngoài viết) và rò dữ liệu (lethal trifecta: dữ liệu riêng + nội dung không tin cậy +
kênh gửi ra ngoài).

## Quyết định

### 1. Lịch chạy (module `automation`)
- Một **schedule** = agent đã publish + lời nhắn (prompt) + kiểu kích hoạt:
  - `cron`: biểu thức 5 trường (`phút giờ ngày tháng thứ`, hỗ trợ `*`, `,`, `-`, `/`, `@hourly`,
    `@daily`, `@weekly`) theo múi giờ của schedule (mặc định `Asia/Ho_Chi_Minh`); tối đa 12 lần/giờ.
  - `gmail`: kiểm tra mail mới mỗi N phút qua connection Gmail của chủ schedule.
- Tự viết bộ phân tích cron (hàm thuần, có test) — không thêm dependency.
- Worker: cron mỗi phút duyệt các tenant (`identity.tenants`), trong từng tenant (RLS) **nhận** các
  schedule đến hạn bằng `FOR UPDATE SKIP LOCKED` và ghi ngay `next_run_at` mới (không chạy bù lần
  lỡ khi worker tắt), rồi đẩy job `run_schedule` vào hàng đợi arq.
- Mỗi lần chạy ghi `automation.schedule_runs` (UNIQUE `schedule_id, trigger_ref`) → mail đã xử lý
  không bị chạy lại; lỗi liên tiếp 5 lần → tự tắt schedule.
- Giới hạn: 20 schedule/người.

### 2. Danh tính khi chạy tự động
- Run chạy dưới **chính chủ schedule** nhưng **không mang vai trò** (`roles = ∅`): dùng được agent
  đã publish và connection cá nhân của chủ, không dùng được quyền admin/builder lúc vắng mặt.
- Thao tác cần duyệt → tạo yêu cầu duyệt như chat thường (người yêu cầu = chủ schedule); run dừng ở
  `awaiting_approval`, chủ duyệt ở trang Duyệt / mở hội thoại để chạy tiếp. Không có gì tự gửi đi.
- Thread mang tiêu đề `⏰ <tên schedule>`; trang Tự động hóa có lịch sử từng lần chạy.

### 3. Gmail (connector `gmail`, OAuth Google — ADR-0018)
- Tool: `gmail.search`, `gmail.read_message` (read, **untrusted**), `gmail.create_draft` (write),
  `gmail.send` (**external_send** → luôn duyệt). HTML chuyển sang text, không tải ảnh/link.
- Trigger `gmail.new_message`: polling History API (không dùng Pub/Sub push để khỏi mở endpoint cho
  Google); lần đầu chỉ ghi mốc `historyId`; tối đa 10 mail/lần; bỏ mail tự gửi, nháp, spam; lọc
  tùy chọn theo người gửi/tiêu đề.
- Plugin SDK thêm `TriggerSpec` + `Plugin.poll_trigger(...)` (bổ sung, không đổi api_version).

### 4. Taint & lethal trifecta
- Nội dung do trigger mang vào (email) được bọc nhãn `⟦DỮ LIỆU BÊN NGOÀI…⟧` ngay trong tin nhắn
  của run → run nhiễm từ đầu; taint tính trên **mọi** block của lịch sử (không chỉ tool_result).
- Run đã nhiễm: mọi `write`/`external_send` cần duyệt (đã có, ADR-0016) **và** tool đọc có truy cập
  mạng (`network: true`, ví dụ `web.fetch`) chỉ được gọi URL **đã xuất hiện nguyên văn** trong hội
  thoại; URL "mới" (kiểu `https://attacker/?q=<dữ liệu>`) phải duyệt.

### 5. DLP
- Bộ dò thuần (`shared_kernel.dlp`): email, số điện thoại VN, CCCD/CMND, số thẻ (Luhn), IBAN/số tài
  khoản dài, khóa bí mật (khóa riêng PEM, `sk-…`, `AKIA…`, `ghp_…`, `xox…`, JWT, `ak_…`).
- `AgentSpec.dlp`: `off` | `mask` (mặc định: khóa bí mật, CCCD, số thẻ) | `strict` (thêm email,
  SĐT) — che trong câu trả lời gửi qua link công khai (cả khi stream và khi tải lại), API key,
  Telegram. Chat nội bộ không che (người dùng đã được xác thực và có quyền với dữ liệu).| `mask` (mặc định `mask` cho kênh ngoài) — che dữ liệu nhạy cảm trong câu
  trả lời gửi qua link công khai, API key, Telegram.
- Tham số của tool `external_send` chứa **khóa bí mật** → từ chối luôn (không cho duyệt).

### 6. Hoãn
- Plan & Execute nhiều bước dài (graph mới): Phase 6b.
- Gmail push (Pub/Sub), webhook trigger tùy biến: khi có nhu cầu.

## Hệ quả
+ Agent làm việc định kỳ/phản ứng với mail mà không nới quyền: chạy không vai trò, mọi thao tác
  rủi ro vẫn chờ người duyệt.
+ Prompt injection qua email không tự gửi dữ liệu ra ngoài được (duyệt + chặn URL mới + DLP).
- Run tự động có thao tác cần duyệt sẽ dừng đến khi chủ duyệt.
- Cron tối thiểu 5 phút; lịch lỡ khi worker tắt không chạy bù.
