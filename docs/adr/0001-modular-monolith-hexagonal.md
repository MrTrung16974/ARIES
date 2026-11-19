# ADR-0001: Modular monolith + Hexagonal

- Trạng thái: Accepted

## Bối cảnh
Cần hệ thống dễ mở rộng, nâng cấp, nhưng đội nhỏ và triển khai on-prem.

## Quyết định
Một codebase, chia module theo bounded context; mỗi module 4 lớp api/application/domain/infrastructure; phụ thuộc vào Port; ranh giới kiểm bằng import-linter.

## Hệ quả
+ Vận hành đơn giản, tách service sau bằng cách thay adapter.
- Cần kỷ luật giữ ranh giới module.
