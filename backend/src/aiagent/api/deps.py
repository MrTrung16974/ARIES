from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Annotated, cast

import structlog
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from aiagent.bootstrap.container import Container
from aiagent.modules.audit.domain import Outcome
from aiagent.modules.identity.domain import Principal, Role
from aiagent.shared_kernel.errors import ForbiddenError, UnauthorizedError


def get_container(request: Request) -> Container:
    return cast(Container, request.app.state.container)


ContainerDep = Annotated[Container, Depends(get_container)]

_bearer = HTTPBearer(auto_error=False)


def client_ip(request: Request) -> str | None:
    # uvicorn --proxy-headers chỉ tin X-Forwarded-For từ Traefik (FORWARDED_ALLOW_IPS).
    return request.client.host if request.client else None


async def get_principal(
    container: ContainerDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> Principal:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise UnauthorizedError()
    principal = await container.token_verifier.verify(credentials.credentials)
    structlog.contextvars.bind_contextvars(tenant_id=principal.tenant_id, subject=principal.subject)
    return principal


def require_roles(*roles: Role) -> Callable[..., Awaitable[Principal]]:
    """Dependency kiểm vai trò; Owner luôn được phép. Từ chối được ghi audit `authz.denied`."""

    async def dependency(
        request: Request,
        principal: Annotated[Principal, Depends(get_principal)],
        container: ContainerDep,
    ) -> Principal:
        if principal.has_any_role(*roles):
            return principal
        await container.audit.record_safely(
            tenant_id=principal.tenant_id,
            actor_id=principal.subject,
            action="authz.denied",
            outcome=Outcome.DENIED,
            resource_type="endpoint",
            resource_id=f"{request.method} {request.url.path}",
            ip=client_ip(request),
            details={"required_roles": sorted(r.value for r in roles)},
        )
        raise ForbiddenError()

    return dependency
