# ADR-0014: Runtime — LangGraph + Anthropic SDK trực tiếp, lịch sử hội thoại do ta lưu

- Trạng thái: Accepted
- Ngày: 2026-11-19

## Quyết định
1. Graph dùng **LangGraph `StateGraph`** với state riêng; node gọi `ChatModelPort` của ta,
   **không** dùng chat model của LangChain.
2. Adapter Claude dùng **SDK chính thức `anthropic`** để kiểm soát đầy đủ: streaming, `effort`,
   thinking block (trả lại nguyên vẹn), prompt caching, `stop_reason` (`refusal`, `max_tokens`,
   `pause_turn`), usage.
3. Lịch sử hội thoại lưu trong bảng `conversations.messages` (RLS) dưới dạng **content block gốc**;
   đây cũng là nguồn cho log/feedback.
4. LangGraph checkpointer Postgres được đưa vào ở Phase 3 khi cần interrupt/resume (HITL).
5. Adapter OpenAI-compatible (vLLM) cho model tự host bổ sung sau, cùng `ChatModelPort`.

## Hệ quả
+ Không phụ thuộc lớp trừu tượng LangChain cho phần quan trọng nhất (gọi model).
+ Log hội thoại và dữ liệu replay là một.
- Tự viết vòng ReAct (vài chục dòng) và chuyển đổi tool schema.
