# ADR-0005: RunEvent contract cho streaming

- Trạng thái: Accepted

## Quyết định
Stream SSE với RunEvent {run_id, seq, type, payload, ts}, type thuộc token|message|tool_start|tool_end|citation|approval_required|error|usage|done. Frontend và channel chỉ phụ thuộc contract này.

## Hệ quả
+ Đổi engine/LLM không ảnh hưởng UI, Telegram.
