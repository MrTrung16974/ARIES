from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from aiagent.adapters.db import Database
from aiagent.modules.identity.domain import Principal, UserProfile
from aiagent.shared_kernel.errors import ForbiddenError

_UPSERT = text(
    """
    INSERT INTO identity.users (tenant_id, subject, username, email, last_seen_at)
    VALUES (:tenant_id, :subject, :username, :email, now())
    ON CONFLICT (tenant_id, subject) DO UPDATE
       SET username = EXCLUDED.username,
           email = EXCLUDED.email,
           last_seen_at = now()
    RETURNING tenant_id, subject, username, email, created_at, last_seen_at
    """
)


class SqlUserRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def touch(self, principal: Principal) -> UserProfile:
        try:
            async with self._db.tenant_session(principal.tenant_id) as session:
                row = (
                    await session.execute(
                        _UPSERT,
                        {
                            "tenant_id": principal.tenant_id,
                            "subject": principal.subject,
                            "username": principal.username,
                            "email": principal.email,
                        },
                    )
                ).one()
        except IntegrityError as exc:  # tenant không tồn tại
            raise ForbiddenError("Tenant không hợp lệ.") from exc
        return UserProfile(
            tenant_id=row.tenant_id,
            subject=row.subject,
            username=row.username,
            email=row.email,
            created_at=row.created_at,
            last_seen_at=row.last_seen_at,
        )
