# ADR-0007: Keycloak + RBAC/ABAC bằng Cedar

- Trạng thái: Accepted

## Quyết định
Keycloak làm IdP (OIDC, PKCE, MFA, có thể federate LDAP/AD). Phân quyền tài nguyên bằng Cedar (cedarpy, in-process), policy là code. tenant_id + Postgres RLS từ đầu.

## Phương án khác
OPA (sidecar) — mạnh nhưng thêm một service; giữ được qua PolicyPort nếu cần đổi.
