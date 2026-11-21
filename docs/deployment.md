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

## 5. Phase 3b — vận hành OpenBao, egress, MCP, OAuth (ADR-0018)

### Mạng trong Compose
| Mạng | Internet | Thành phần |
|---|---|---|
| `aiagent_app` | ✗ (internal) | traefik, web, api, worker, keycloak, searxng, squid, mcp-demo |
| `aiagent_data` | ✗ (internal) | postgres, redis, openbao, (api, worker, keycloak, migrate) |
| `aiagent_edge` | ✓ | traefik, keycloak (publish cổng), squid, searxng |

API/worker **không có đường ra Internet nào ngoài Squid**:
- `squid:3128` — lưu lượng hệ thống, chỉ domain trong `deploy/squid/allowed-domains.txt`
  (Anthropic, Google…). Thêm domain cho MCP server HTTP / nhà cung cấp OAuth mới rồi
  `docker compose restart squid`.
- `squid:3129` — `web.fetch`: mọi domain công khai, chặn mọi IP nội bộ/riêng (kể cả tên miền
  công khai trỏ về IP nội bộ).
- Kết nối CSDL ngoài Docker (connector Postgres): gắn thêm mạng/route cho api có chủ đích —
  không mở Internet chung.

### OpenBao (server mode + raft + AppRole)
- `openbao-init` chạy mỗi lần `docker compose up`: lần đầu init, mỗi lần unseal và cấp
  `secret_id` mới cho API (file trong volume `baoapprole`, chỉ api/worker mount).
- OpenBao khởi động lại một mình sẽ **bị khóa** (API báo `vault: false` ở `/readyz`) — chạy lại:
  `docker compose up -d openbao-init`.
- **Prod:** không dùng `openbao-init` (unseal key nằm trên volume); dùng Shamir nhiều người giữ
  hoặc auto-unseal (Transit/KMS/HSM), bật TLS listener, sao lưu snapshot raft định kỳ.

### MCP server
- Admin đăng ký ở trang **MCP**. Kiểu `stdio` chỉ chọn được lệnh trong
  `AIAGENT_MCP_STDIO_COMMANDS` (JSON `{"tên": ["lệnh", "tham số"…]}`) do người vận hành cài.
- Thử nhanh: `docker compose --profile demo up -d mcp-demo` rồi đăng ký URL
  `http://mcp-demo:8765/mcp` với header `X-Api-Key` = `MCP_DEMO_API_KEY` trong `.env`.

### OAuth cho connector
- `AIAGENT_PUBLIC_BASE_URL` phải là địa chỉ trình duyệt dùng; redirect URI =
  `<PUBLIC_BASE_URL>/api/v1/oauth/callback` (đăng ký ở nhà cung cấp).
- Google: đặt `OAUTH_GOOGLE_CLIENT_ID`/`OAUTH_GOOGLE_CLIENT_SECRET`.
- SSO nội bộ (Keycloak, để thử luồng đầy đủ): đặt `OAUTH_KEYCLOAK_CLIENT_SECRET` rồi
  `docker compose --profile demo run --rm keycloak-setup`.

## 6. Phase 4 — Kho tri thức (ADR-0019)

- `embedder` (TEI CPU, `BAAI/bge-m3`) tải model ~2,3 GB lần đầu vào volume `hfmodels` qua Squid
  (`.huggingface.co`, `.hf.co` trong allowlist). Môi trường không có Internet: nạp sẵn volume.
- RAM: embedder giới hạn 5 GB, `--max-batch-tokens 2048` (đặt 8192 thì bước warmup vượt giới hạn
  bộ nhớ → bị OOM-kill lặp và có thể làm Docker engine treo). Docker Desktop mặc định dùng tối đa
  50% RAM máy cho VM — chỉnh `.wslconfig` nếu cần.
- Reranker (`bge-reranker-v2-m3`) tùy chọn: `docker compose --profile rerank up -d reranker`, đặt
  `RERANKER_URL=http://reranker` rồi restart api/worker.
- Tệp gốc nằm ở volume `kbfiles` (`/data/kb` trong api/worker); sao lưu cùng Postgres.
- Ingest chạy ở `worker` (arq, tối đa 2 job song song, mỗi job ≤ 15 phút).
