# ADR-0013: Không làm workflow canvas — tạo agent bằng ngôn ngữ tự nhiên

- Trạng thái: Accepted
- Ngày: 2026-11-19

## Bối cảnh
Sản phẩm tham chiếu (Dify) cho dựng agent bằng canvas kéo-thả node. Yêu cầu: cùng nhóm tính năng
(agent, knowledge, tool, model, publish, log) nhưng không bắt người dùng vẽ workflow.

## Quyết định
- Không có workflow/chatflow canvas.
- Agent được tạo và sửa qua **Agent Architect** (ngôn ngữ tự nhiên), slash command và form nâng cao;
  cả ba đi qua cùng Command API, cùng validate, version, audit.
- Luồng xử lý lúc chạy do graph template (ReAct, Plan & Execute, Supervisor) và LLM điều phối;
  tác vụ định kỳ/sự kiện tạo bằng `/schedule` và trigger.
- Giữ `graph_type: custom` trong schema để mở đường nếu sau này thật sự cần.

## Hệ quả
+ Tạo agent nhanh, không cần học công cụ vẽ; agent-as-code (YAML) tự nhiên.
+ Ít bề mặt UI phải bảo trì.
- Quy trình có điều kiện phức tạp, cần tính xác định cao khó diễn đạt hơn canvas.
- Chất lượng phụ thuộc Architect → cần eval bắt buộc và golden dataset.
