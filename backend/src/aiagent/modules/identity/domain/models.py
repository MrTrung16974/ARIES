from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class Role(StrEnum):
    """Vai trò cấp realm trong Keycloak (docs/security.md mục 2)."""

    OWNER = "owner"
    ADMIN = "admin"
    BUILDER = "builder"
    USER = "user"
    VIEWER = "viewer"
    AUDITOR = "auditor"


@dataclass(frozen=True)
class Principal:
    """Danh tính đã xác thực của người gọi API."""

    subject: str
    tenant_id: str
    username: str | None
    email: str | None
    roles: frozenset[Role]

    def has_any_role(self, *roles: Role) -> bool:
        return Role.OWNER in self.roles or bool(self.roles.intersection(roles))


@dataclass(frozen=True)
class UserProfile:
    tenant_id: str
    subject: str
    username: str | None
    email: str | None
    created_at: datetime
    last_seen_at: datetime
