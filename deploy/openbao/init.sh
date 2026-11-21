#!/bin/sh
# Khởi tạo / mở khóa OpenBao và cấp AppRole cho API (ADR-0018). Chạy mỗi lần `compose up`.
# DEV/STAGING: unseal key + root token lưu ở volume `baoinit`. PROD: KHÔNG dùng script này —
# dùng Shamir nhiều người giữ hoặc auto-unseal (xem docs/deployment.md).
set -eu
INIT=/init/init.txt
OUT=/approle
rm -f /tmp/aries-ready

status() { bao status >/dev/null 2>&1; echo $?; }   # 0 = mở, 2 = đang khóa, 1 = lỗi

i=0
while [ "$(status)" = "1" ]; do
  i=$((i + 1)); [ "$i" -gt 60 ] && { echo "OpenBao không phản hồi" >&2; exit 1; }
  sleep 1
done

if ! bao status | grep -q 'Initialized *true'; then
  echo "Khởi tạo OpenBao lần đầu"
  bao operator init -key-shares=1 -key-threshold=1 > "$INIT"
  chmod 600 "$INIT"
fi
KEY=$(awk '/Unseal Key 1:/ {print $4}' "$INIT")
ROOT=$(awk '/Initial Root Token:/ {print $4}' "$INIT")

if [ "$(status)" = "2" ]; then
  bao operator unseal "$KEY" >/dev/null
fi

export BAO_TOKEN="$ROOT"
i=0
until wget -q -O /dev/null "$BAO_ADDR/v1/sys/health"; do   # 200 = đã mở khóa + active (raft có leader)
  i=$((i + 1)); [ "$i" -gt 60 ] && { echo "OpenBao chưa sẵn sàng" >&2; exit 1; }
  sleep 1
done

bao secrets list | grep -q '^secret/' || bao secrets enable -path=secret kv-v2
bao auth list | grep -q '^approle/' || bao auth enable approle

bao policy write aries-api - <<'POLICY'
# API chỉ đọc/ghi secret của ARIES, không làm gì khác.
path "secret/data/aries/*"     { capabilities = ["create", "read", "update", "delete"] }
path "secret/metadata/aries/*" { capabilities = ["read", "delete", "list"] }
POLICY

bao write auth/approle/role/aries-api \
  token_policies=aries-api token_ttl=1h token_max_ttl=4h \
  secret_id_ttl=720h secret_id_num_uses=0 >/dev/null

umask 022
bao read -field=role_id auth/approle/role/aries-api/role-id > "$OUT/role_id"
bao write -f -field=secret_id auth/approle/role/aries-api/secret-id > "$OUT/secret_id"
echo "OpenBao sẵn sàng (đã mở khóa, AppRole aries-api đã cấp secret_id mới)"
touch /tmp/aries-ready   # healthcheck của openbao-init: api/worker chờ mốc này

# Canh gác (DEV/STAGING): OpenBao khởi động lại sẽ bị khóa → mọi secret không đọc được.
# BAO_WATCH=1: ở lại và tự mở khóa mỗi khi phát hiện đang khóa. PROD dùng auto-unseal (KMS/transit).
if [ "${BAO_WATCH:-0}" = "1" ]; then
  echo "Canh gác: tự mở khóa OpenBao khi bị khóa lại (kiểm tra mỗi ${BAO_WATCH_INTERVAL:-10}s)"
  while true; do
    sleep "${BAO_WATCH_INTERVAL:-10}"
    if [ "$(status)" = "2" ]; then
      if bao operator unseal "$KEY" >/dev/null 2>&1; then
        echo "$(date -u +%FT%TZ) OpenBao bị khóa (khởi động lại?) — đã tự mở khóa"
      else
        echo "$(date -u +%FT%TZ) Mở khóa OpenBao thất bại, sẽ thử lại" >&2
      fi
    fi
  done
fi
