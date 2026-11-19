# Backend — AI Agent Platform

FastAPI + LangGraph, kiến trúc modular monolith / hexagonal. Xem [docs/architecture.md](../docs/architecture.md).

## Chạy local (cần Python 3.12 + uv)

```bash
uv sync
uv run uvicorn aiagent.main:create_app --factory --reload
uv run pytest
```

## Chạy bằng Docker (không cần cài Python)

```bash
docker compose -f ../deploy/docker-compose.yml run --rm backend-dev pytest
```

## Kiểm tra chất lượng

```bash
uv run ruff check . && uv run ruff format --check .
uv run mypy
uv run lint-imports
uv run bandit -c pyproject.toml -r src
```

## Cấu trúc

```
src/aiagent/
  bootstrap/      settings, logging, plugin loader, DI container (composition root)
  shared_kernel/  lỗi, event bus, clock
  plugin_sdk/     API công khai cho plugin (semver)
  api/            dependency, middleware, xử lý lỗi, endpoint hệ thống
  adapters/       hạ tầng dùng chung (Postgres)
  modules/        identity, audit (mỗi module: api / application / domain / infrastructure)
  workers/        arq worker
src/aiagent_plugins/  plugin built-in (chỉ dùng plugin_sdk)
migrations/       Alembic (chạy bằng role owner)
```
