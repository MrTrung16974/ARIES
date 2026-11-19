from __future__ import annotations

from aiagent.modules.identity.domain import Principal, UserProfile, UserRepositoryPort


class IdentityService:
    def __init__(self, users: UserRepositoryPort) -> None:
        self._users = users

    async def current_user(self, principal: Principal) -> UserProfile:
        return await self._users.touch(principal)
