# syntax=docker/dockerfile:1.7
# Image dùng chung cho api, worker và migrate.

FROM python:3.12-slim AS base
# Mạng chậm/qua proxy: tăng timeout tải gói (mặc định 30s) và giảm số tải song song.
# Có mirror PyPI nội bộ: docker compose build --build-arg UV_INDEX_URL=https://mirror/simple
ARG UV_HTTP_TIMEOUT=300
ARG UV_CONCURRENT_DOWNLOADS=4
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_HTTP_TIMEOUT=${UV_HTTP_TIMEOUT} \
    UV_CONCURRENT_DOWNLOADS=${UV_CONCURRENT_DOWNLOADS} \
    PATH="/opt/venv/bin:$PATH"
COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /uvx /bin/
WORKDIR /app

# ---------------------------------------------------------------- dev (có công cụ test/lint)
FROM base AS dev
ARG UV_INDEX_URL
COPY pyproject.toml uv.lock* README.md ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --no-install-project
COPY . .
RUN --mount=type=cache,target=/root/.cache/uv uv sync

# ---------------------------------------------------------------- build (chỉ dependency runtime)
FROM base AS build
ARG UV_INDEX_URL
COPY pyproject.toml uv.lock* README.md ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --no-dev --no-install-project
COPY src ./src
# --reinstall-package: không tái dùng wheel cache của chính project (version không đổi giữa các lần build)
RUN --mount=type=cache,target=/root/.cache/uv uv sync --no-dev --no-editable --reinstall-package aiagent

# ---------------------------------------------------------------- runtime
FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH="/opt/venv/bin:$PATH"
RUN groupadd --system app && useradd --system --gid app --home /app app
WORKDIR /app
COPY --from=build /opt/venv /opt/venv
COPY alembic.ini ./
COPY migrations ./migrations
# Tệp gốc của kho tri thức (volume kbfiles) — app ghi được.
RUN mkdir -p /data/kb && chown app:app /data/kb
USER app
EXPOSE 8000
CMD ["uvicorn", "aiagent.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
