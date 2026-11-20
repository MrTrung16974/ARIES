#!/bin/bash
# Tạo/cập nhật client OAuth "aries-connector" (confidential, PKCE S256) cho connector "SSO nội bộ".
# Chạy lại nhiều lần vẫn an toàn:  docker compose --profile demo run --rm keycloak-setup
set -euo pipefail
: "${OAUTH_KEYCLOAK_CLIENT_SECRET:?Đặt OAUTH_KEYCLOAK_CLIENT_SECRET trong deploy/.env}"
KC=/opt/keycloak/bin/kcadm.sh
$KC config credentials --server http://keycloak:8080 --realm master \
  --user "$KEYCLOAK_ADMIN_USER" --password "$KEYCLOAK_ADMIN_PASSWORD" >/dev/null

REDIRECT="${PUBLIC_BASE_URL:-http://localhost}/api/v1/oauth/callback"
ID=$($KC get clients -r aiagent -q clientId=aries-connector --fields id --format csv --noquotes | head -1)
if [ -z "$ID" ]; then
  $KC create clients -r aiagent -s clientId=aries-connector -s enabled=true \
    -s protocol=openid-connect -s publicClient=false -s standardFlowEnabled=true \
    -s directAccessGrantsEnabled=false -s serviceAccountsEnabled=false >/dev/null
  ID=$($KC get clients -r aiagent -q clientId=aries-connector --fields id --format csv --noquotes | head -1)
fi
$KC update "clients/$ID" -r aiagent \
  -s "secret=$OAUTH_KEYCLOAK_CLIENT_SECRET" \
  -s "redirectUris=[\"$REDIRECT\"]" \
  -s 'attributes."pkce.code.challenge.method"=S256' >/dev/null
echo "Client aries-connector sẵn sàng (redirect $REDIRECT)"
