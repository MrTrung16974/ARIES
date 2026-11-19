# AI Agent Platform

Nền tảng xây dựng và vận hành nhiều AI agent (LangGraph) có RAG, Skill/Connector, cấu hình qua web, triển khai on-premise.

- Thiết kế: [docs/](docs/README.md) — kiến trúc, bảo mật, tích hợp, triển khai, roadmap, ADR.
- Backend: [backend/](backend/README.md) — Python 3.12, FastAPI, (LangGraph từ Phase 1).
- Frontend: [frontend/](frontend/) — Nx + Angular, PrimeNG, đăng nhập OIDC (Keycloak).
- Hạ tầng: [deploy/](deploy/) — Docker Compose (Traefik, Postgres+pgvector, Redis, Keycloak, OpenBao).

## Chạy toàn bộ bằng Docker

Yêu cầu: Docker Desktop đang chạy.

```bash
bash deploy/scripts/gen-env.sh        # tạo deploy/.env với secret ngẫu nhiên (lần đầu)
docker compose -f deploy/docker-compose.yml up -d --build
```

| Địa chỉ | Dịch vụ |
|---|---|
| http://localhost | Web (Angular) + `/api` (FastAPI) qua Traefik |
| http://localhost/api/docs | OpenAPI (không bật ở prod) |
| http://localhost:8080 | Keycloak (admin console: tài khoản trong `deploy/.env`) |

Tài khoản dev (chỉ dùng cho môi trường dev) nằm trong `deploy/keycloak/realm-aiagent.json`:
`owner.dev`, `builder.dev`, `user.dev`, `auditor.dev`.

## Test backend trong container

```bash
docker compose -f deploy/docker-compose.yml run --rm backend-dev pytest
```

## Frontend dev server

```bash
cd frontend && npm install && npx nx serve web   # http://localhost:4200, proxy /api → http://localhost
```

## Bật Claude (tùy chọn)

Mặc định chỉ có model giả `fake-echo` (chạy được mà không cần key; gõ `/tool <tên_tool> <json>` để thử gọi tool).
Để dùng Claude, điền key vào `deploy/.env` (không commit file này) rồi khởi động lại api:

```bash
docker compose -f deploy/docker-compose.yml up -d api worker
```

Biến cần đặt trong `deploy/.env`: `ANTHROPIC_API_KEY=...`. Model cho phép: `claude-opus-5-5` (mặc định),
`claude-sonnet-5-5`, `claude-haiku-5-5`.
