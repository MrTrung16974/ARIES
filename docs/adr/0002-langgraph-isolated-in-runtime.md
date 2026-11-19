# ADR-0002: LangGraph chỉ nằm trong module runtime

- Trạng thái: Accepted

## Quyết định
Chỉ runtime/infrastructure import LangGraph/LangChain. Bên ngoài dùng AgentSpec và RunEvent contract.

## Hệ quả
+ Nâng cấp hoặc thay engine chỉ đụng một chỗ.
- Phải viết lớp chuyển đổi event.
