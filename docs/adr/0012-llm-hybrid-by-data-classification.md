# ADR-0012: LLM hybrid theo phân loại dữ liệu

- Trạng thái: Accepted
- Ngày: 2026-11-18

## Bối cảnh
- Triển khai on-prem, **chưa có GPU**, giai đoạn đầu dưới 50 người dùng.
- Dữ liệu thường được phép gửi tới API LLM trên cloud; dữ liệu nhạy cảm thì không.
- Agent cần tool calling nhiều bước ổn định — model mở chạy trên CPU không đáp ứng được.

## Quyết định
1. LLM chính: **Claude API (Anthropic)** qua `ChatModelPort` / `AnthropicAdapter`.
   - Mặc định agent: `claude-opus-5-5`; chọn được `claude-sonnet-5-5`, `claude-haiku-5-5` theo agent.
   - Tác vụ phụ (đặt tiêu đề, phân loại injection, tóm tắt lịch sử): `claude-haiku-5-5`.
2. Dữ liệu gắn nhãn `public | internal | confidential | restricted`; nhãn lan truyền trong run.
3. **ModelRouter**: `restricted` chỉ đi tới model tự host. Chưa có GPU → `block` (mặc định) hoặc `redact` bằng DLP.
4. Viết sẵn adapter OpenAI-compatible (vLLM) + contract test; bật khi có GPU.
5. AgentDefinition dùng `effort` thay cho `temperature` (model Claude hiện tại không nhận tham số sampling).

## Phương án khác
- **Chỉ tự host**: không khả thi khi chưa có GPU; chất lượng agent thấp hơn.
- **Chỉ cloud**: đơn giản nhưng vi phạm yêu cầu với dữ liệu nhạy cảm.

## Hệ quả
+ Chất lượng agent cao ngay từ đầu, không cần đầu tư phần cứng.
+ Có đường nâng cấp rõ ràng khi mua GPU (chỉ bật adapter, không đổi kiến trúc).
- Chi phí API theo lượng dùng → cần quota, prompt caching, đo `usage` từ Phase 1.
- Tác vụ dính dữ liệu `restricted` tạm thời không dùng được agent cho tới khi có GPU.
- Phụ thuộc kết nối internet tới `api.anthropic.com` (qua egress proxy).
