#!/usr/bin/env bash
# Tạo deploy/.env từ .env.example với secret ngẫu nhiên (chỉ dùng cho dev/staging).
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ -f .env ]]; then
  echo ".env đã tồn tại — không ghi đè." >&2
  exit 1
fi

rand() { openssl rand -base64 48 | tr -dc 'A-Za-z0-9' | head -c 32; }

while IFS= read -r line; do
  if [[ "$line" == *=change-me ]]; then
    echo "${line%=change-me}=$(rand)"
  else
    echo "$line"
  fi
done < .env.example > .env

echo "Đã tạo deploy/.env"
