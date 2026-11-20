# ADR-0017: Master agent — một đầu mối nhận yêu cầu và giao việc cho các agent khác

- Trạng thái: Accepted (Phase 3c, 2026-11-20)
- Ngày: 2026-11-20

## Bối cảnh

Khi có nhiều agent (tra cứu đơn hàng, tìm kiếm web, soạn email…), người dùng không muốn phải tự
biết nên mở agent nào. Cần **một agent chủ (Master)**: người dùng chỉ nói với Master, Master hiểu
yêu cầu, chia việc và **giao (delegate)** cho các agent chuyên trách, rồi tổng hợp kết quả.

Ràng buộc đã có: không có workflow canvas (ADR-0013), mọi tool qua ToolGateway + Policy + HITL
(ADR-0010, ADR-0016), LLM theo phân loại dữ liệu (ADR-0012), kết nối kiểm quyền theo người chạy.

## Quyết định

### 1. Mô hình: Supervisor + agent-as-tool
- Master là một agent có `graph_type: "supervisor"`. Mỗi agent thành viên được đưa cho Master
  dưới dạng **một tool ủy quyền** `delegate__<alias>(task, context)` — mô tả tool lấy từ
  `description` + `when_to_use` của agent đó. Model của Master tự chọn giao cho ai (định tuyến
  bằng LLM), có thể gọi **song song** nhiều agent trong cùng một lượt.
- Không có cạnh/luồng vẽ tay: thứ tự và cách phối hợp do Master quyết định khi chạy (đúng
  ADR-0013). Yêu cầu nhiều bước: Master lập kế hoạch ngắn (danh sách việc) rồi giao từng phần.
- Không có agent phù hợp → Master tự trả lời trong phạm vi của mình hoặc nói rõ là chưa có agent
  làm được việc đó (và gợi ý tạo agent bằng Architect cho Builder).

### 2. Hai loại Master
| Loại | Thành viên | Ai tạo |
|---|---|---|
| **ARIES Master** (mặc định, một cho mỗi tenant) | Tự động = các agent đã publish mà **người đang chat được dùng**, lọc theo allowlist của Admin | Hệ thống tạo sẵn; Admin cấu hình |
| **Team supervisor** (tùy chọn) | Danh sách cố định trong spec: `team: [{agent_id, alias, when_to_use}]` | Builder, qua Architect hoặc form |

AgentSpec bổ sung (trường mới có giá trị mặc định nên vẫn là schema v2, không cần migrator):
```yaml
graph_type: supervisor
team:
  mode: auto | fixed          # auto = ARIES Master
  members: [{agent_id, alias, when_to_use, pinned_version?}]
  max_delegations_per_run: 8
  max_parallel: 3
```

### 3. Lần ủy quyền = run con (child run)
- Mỗi lần giao việc tạo **run con** của agent thành viên (`parent_run_id`, `delegation_id`), trên
  một thread con gắn với thread của Master. Run con chạy bằng **chính danh tính người dùng**,
  không phải của Master → agent thành viên chỉ dùng được connection/tool mà người dùng có quyền
  (không leo thang đặc quyền qua Master).
- Agent thành viên chỉ nhận `task` + `context` Master tự viết (tối thiểu hóa dữ liệu), không thấy
  toàn bộ hội thoại của Master.
- Kết quả trả về Master là `tool_result` gồm câu trả lời cuối + tool đã dùng; quá dài thì cắt.
- **Độ sâu tối đa = 1**: agent thành viên không được ủy quyền tiếp (chặn vòng lặp, khó kiểm soát).

### 4. An toàn
- **Taint lan lên**: run con đã nhiễm (đọc web/DB) → kết quả trả về bị bọc nhãn dữ liệu bên ngoài
  và Master cũng bị đánh dấu nhiễm (thao tác ghi sau đó của Master cần duyệt).
- **HITL lồng nhau**: run con dừng chờ duyệt → Master cũng dừng (`awaiting_approval`), thẻ duyệt
  hiển thị ngay trong hội thoại với Master kèm "do agent X yêu cầu". Duyệt xong: tiếp tục run con
  trước, kết quả quay về Master rồi Master chạy tiếp (mở rộng cơ chế resume của ADR-0016).
- **Phân loại dữ liệu**: Master chỉ được giao cho agent có `data_classification` ≤ của Master, để
  kết quả mật không chảy sang model cloud của Master. ARIES Master mặc định `internal`.
- **Ngân sách chung**: token/chi phí/thời gian của run con tính vào giới hạn của run Master;
  giới hạn số lần ủy quyền và số run con song song.
- Audit: `delegation.started/finished` (master run, child run, agent đích), không ghi nội dung.

### 5. Hiển thị (RunEvent)
- Sự kiện mới: `delegation_start {delegation_id, agent_id, agent_name, task}`,
  `delegation_end {delegation_id, status, summary}`; sự kiện của run con được chuyển tiếp kèm
  `delegation_id` để UI vẽ cây việc: Master → Agent A (đang chạy tool…) / Agent B (xong).
- Trang chủ có ô chat "Hỏi ARIES" mặc định nói chuyện với ARIES Master.

### 6. Architect
- Khi tạo agent mới, Architect hỏi/đề xuất `when_to_use` (một câu mô tả khi nào nên giao việc) và
  mặc định thêm agent vào ARIES Master sau khi publish.
- Lệnh: `/team create`, `/team add <agent>`, `/team remove <agent>`; có thể tạo team supervisor
  bằng lời ("tạo nhóm gồm agent đơn hàng và agent email để xử lý khiếu nại").
- Eval định tuyến: Architect sinh bộ câu hỏi kèm agent đáng ra phải được giao; đo tỉ lệ giao đúng.

## Phương án khác
- **Router phân loại một lần** (chọn đúng một agent rồi chuyển hẳn): rẻ hơn nhưng không chia
  được việc nhiều phần, không tổng hợp được. Giữ làm tối ưu sau (Master có thể "chuyển hẳn" khi
  chỉ cần một agent).
- **Swarm/handoff tự do giữa các agent**: linh hoạt nhưng khó kiểm soát quyền, chi phí, khó audit.
- **Gộp mọi tool vào một agent lớn**: prompt phình to, chọn tool kém, mất ranh giới quyền theo agent.

## Hệ quả
+ Người dùng có một đầu mối; agent chuyên trách vẫn nhỏ, dễ kiểm thử và phân quyền riêng.
+ Tái dùng runtime, ToolGateway, HITL, Credential Broker sẵn có — delegation chỉ là một loại tool.
- Tốn token hơn (thêm một tầng model) và chậm hơn; cần cache prompt và giới hạn ngân sách.
- Chất lượng phụ thuộc mô tả `when_to_use` của từng agent → Architect phải viết tốt, có eval.
