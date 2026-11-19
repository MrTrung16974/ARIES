# ADR-0006: Triển khai on-premise

- Trạng thái: Accepted

## Quyết định
Docker Compose (dev/staging/prod giai đoạn đầu), Kubernetes khi cần scale. Phân vùng DMZ/App/Data, egress qua Squid allowlist. Thành phần tự host: Postgres+pgvector, Redis, object storage S3-compatible (chọn ở Phase 3), OpenBao, Keycloak, SearXNG, embedding bge-m3.

## Hệ quả
+ Dữ liệu nằm trong hạ tầng công ty.
- Tự chịu vận hành, backup, cập nhật bảo mật.
