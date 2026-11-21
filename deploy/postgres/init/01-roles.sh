#!/bin/bash
# Chạy MỘT lần khi khởi tạo volume Postgres.
# - aiagent_owner (POSTGRES_USER): owner, chỉ dùng cho Alembic.
# - aiagent_app: role ứng dụng, không superuser, không BYPASSRLS → RLS luôn có hiệu lực.
# - keycloak: database riêng cho Keycloak.
set -euo pipefail

psql -v ON_ERROR_STOP=1 \
  --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  -v app_pw="$AIAGENT_APP_DB_PASSWORD" \
  -v kc_pw="$KEYCLOAK_DB_PASSWORD" <<'EOSQL'
CREATE ROLE aiagent_app LOGIN PASSWORD :'app_pw'
  NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
GRANT CONNECT ON DATABASE aiagent TO aiagent_app;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;

CREATE ROLE keycloak LOGIN PASSWORD :'kc_pw' NOSUPERUSER NOCREATEDB NOCREATEROLE;
CREATE DATABASE keycloak OWNER keycloak;
EOSQL
