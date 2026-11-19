# Triển khai on-premise

## 1. Service

| Service | Image / công nghệ | Vùng mạng |
|---|---|---|
| `traefik` | Traefik v3 — TLS, routing, rate limit | DMZ + App |
| `web` | Angular build, serve bằng nginx | App |
| `api` | FastAPI (uvicorn/gunicorn) | App |
| `worker` | arq worker: ingest, Drive sync, Gmail polling, trigger | App |
| `keycloak` | Keycloak + Postgres riêng | App |
| `postgres` | PostgreSQL 16 + pgvector | Data |
| `redis` | Redis 7 (queue, cache, rate limit) | Data |
| `openbao` | OpenBao (vault) | Data |
| `objectstore` (Phase 3) | Object storage S3-compatible cho file upload — **chưa chọn**: image cộng đồng `minio/minio` đã ngừng phát hành trên Docker Hub (11/2026); đánh giá Garage / SeaweedFS / bản MinIO tự build sau `FileStoragePort` | Data |
| `searxng` | Web search tự host | App (ra ngoài qua proxy) |
| `squid` | Egress proxy, allowlist domain | App → Internet |
| `clamav` | Quét file | App |
| `embeddings` | TEI/Infinity chạy `bge-m3` (+ reranker) trên CPU | App |
| `llm` (sau, khi có GPU) | vLLM — model mở cho dữ liệu `restricted` | App (GPU) |
| — | Claude API (`api.anthropic.com`) qua Squid | Bên ngoài |
| `langfuse` | Tracing tự host (Phase 5) | App |
| `sandbox` | Docker + gVisor runtime cho code/MCP stdio | Cô lập |

## 2. Mạng

```
Internet ──► [Firewall] ──► DMZ: traefik (chỉ path webhook Telegram + OAuth callback nếu cần public)
LAN ──► traefik ──► web, api
App zone ──► Data zone (postgres, redis, openbao, objectstore)  -- chỉ port cần thiết
App zone ──► squid ──► Internet (allowlist: googleapis, telegram, LLM API, search)
Data zone ──✗ Internet
```

Nếu không muốn mở inbound nào: Telegram dùng long polling, OAuth callback dùng domain nội bộ (trình duyệt người dùng ở LAN/VPN).

## 3. Cấu hình phần cứng tham khảo (giai đoạn đầu)

| Thành phần | Tối thiểu |
|---|---|
| App + Data (không LLM local) | 8 vCPU, 32 GB RAM, 500 GB SSD |
| Embedding bge-m3 | CPU (thêm ~4 vCPU, 8 GB RAM); GPU 8 GB+ khi cần nhanh |
| LLM local (giai đoạn sau) | Tối thiểu 1 GPU 48 GB cho model mở cỡ vừa có tool calling |

## 3b. Ước tính chi phí Claude API (< 50 người)

Giả định thô: 50 người × 20 lượt/ngày × 22 ngày ≈ 22.000 lượt/tháng; mỗi lượt agent ~30K token input (nhiều vòng gọi tool) + ~2K output.

| Model | Giá (input / output, USD / 1M token) | Ước tính / tháng (chưa cache) |
|---|---|---|
| `claude-opus-5-5` | $4 / $20 | ~ $3.500 |
| `claude-sonnet-5-5` | $2 / $10 | ~ $1.750 |
| `claude-haiku-5-5` | $0,10 / $0,50 | ~ $90 |

Prompt caching (đọc cache $0,20/1M với Opus 5.5) có thể giảm đáng kể phần input. Đây chỉ là ước tính bậc độ lớn — đo `usage` thật ở Phase 1 rồi đặt quota.

## 4. Vận hành

- Môi trường: `dev` (Compose local) → `staging` → `prod`.
- Secrets: không để trong `.env` ở prod — inject từ OpenBao (agent/templating).
- Backup: pgBackRest cho Postgres, snapshot OpenBao, replication object storage; thử restore hàng tháng.
- Giám sát: Prometheus + Grafana + Loki; cảnh báo audit qua Telegram/email nội bộ.
- Nâng cấp: image có tag theo version, Alembic migrate trước khi rollout, rollback được.
- Khi cần scale: chuyển sang Kubernetes (Helm), tách `knowledge` và `runtime`.
