"""App factory. Chạy: `uvicorn aiagent.main:create_app --factory`."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from aiagent import __version__
from aiagent.api import system
from aiagent.api.errors import install_error_handlers
from aiagent.api.middleware import RequestContextMiddleware
from aiagent.bootstrap.container import Container, build_container
from aiagent.bootstrap.logging import configure_logging
from aiagent.bootstrap.settings import Settings, get_settings
from aiagent.modules.agents.api.router import router as agents_router
from aiagent.modules.audit.api.router import router as audit_router
from aiagent.modules.builder.api.router import router as builder_router
from aiagent.modules.identity.api.router import router as identity_router
from aiagent.modules.runtime.api.router import router as runtime_router


def create_app(
    settings: Settings | None = None,
    container_factory: Callable[[Settings], Container] = build_container,
) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, json=settings.log_json)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        container = container_factory(settings)
        await container.start()
        app.state.container = container
        try:
            yield
        finally:
            await container.stop()

    expose_docs = not settings.is_prod
    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        lifespan=lifespan,
        docs_url="/api/docs" if expose_docs else None,
        redoc_url=None,
        openapi_url="/api/openapi.json" if expose_docs else None,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,  # dùng Bearer token, không dùng cookie
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
        expose_headers=["X-Request-ID"],
    )
    app.add_middleware(RequestContextMiddleware)
    install_error_handlers(app)

    app.include_router(system.router)
    app.include_router(identity_router)
    app.include_router(audit_router)
    app.include_router(agents_router)
    app.include_router(runtime_router)
    app.include_router(builder_router)
    return app
