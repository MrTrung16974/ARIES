from __future__ import annotations

from typing import Protocol

from aiagent.modules.identity.domain.models import Principal, UserProfile


class TokenVerifierPort(Protocol):
    async def verify(self, token: str) -> Principal:
        """Xác thực access token. Ném `UnauthorizedError` nếu không hợp lệ."""
        ...


class UserRepositoryPort(Protocol):
    async def touch(self, principal: Principal) -> UserProfile:
        """Tạo (JIT) hoặc cập nhật người dùng khi đăng nhập; trả về hồ sơ."""
        ...
