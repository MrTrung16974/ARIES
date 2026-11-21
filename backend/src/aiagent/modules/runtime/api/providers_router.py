"""API quản lý nhà cung cấp LLM (Gemini, Ollama, OpenAI, Claude, Groq, OpenRouter, DeepSeek)."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field

from aiagent.api.deps import ContainerDep, require_roles
from aiagent.modules.identity.domain import Principal, Role
from aiagent.modules.runtime.application import ProviderView

router = APIRouter(prefix="/api/v1/model-providers", tags=["model-providers"])

BuilderOrAdmin = Annotated[Principal, Depends(require_roles(Role.ADMIN, Role.BUILDER))]
AdminOnly = Annotated[Principal, Depends(require_roles(Role.ADMIN))]


class ProviderOut(BaseModel):
    id: str
    name: str
    vendor: str
    description: str
    kind: str
    requires_api_key: bool
    requires_base_url: bool
    default_base_url: str
    docs_url: str
    sample_models: list[str]
    supports_free_only: bool
    configured: bool
    enabled: bool
    source: str
    base_url: str
    has_api_key: bool
    custom_models: list[str]
    available_models: list[str]
    extra: dict[str, Any]
    updated_by: str | None
    updated_at: datetime | None


class ConfigureProviderIn(BaseModel):
    enabled: bool = True
    base_url: str | None = Field(default=None, max_length=500)
    api_key: str | None = Field(default=None, max_length=500)
    models: list[str] = Field(default_factory=list, max_length=100)
    extra: dict[str, Any] = Field(default_factory=dict)


class TestProviderIn(BaseModel):
    base_url: str | None = Field(default=None, max_length=500)
    api_key: str | None = Field(default=None, max_length=500)
    extra: dict[str, Any] | None = None


class ProbeOut(BaseModel):
    ok: bool
    latency_ms: int
    models: list[str]
    error: str | None


def _out(v: ProviderView) -> ProviderOut:
    d = v.descriptor
    return ProviderOut(
        id=d.id,
        name=d.name,
        vendor=d.vendor,
        description=d.description,
        kind=d.kind,
        requires_api_key=d.requires_api_key,
        requires_base_url=d.requires_base_url,
        default_base_url=d.default_base_url,
        docs_url=d.docs_url,
        sample_models=list(d.sample_models),
        supports_free_only=d.supports_free_only,
        configured=v.configured,
        enabled=v.enabled,
        source=v.source,
        base_url=v.base_url,
        has_api_key=v.has_api_key,
        custom_models=v.custom_models,
        available_models=v.available_models,
        extra=v.extra,
        updated_by=v.updated_by,
        updated_at=v.updated_at,
    )


@router.get("", response_model=list[ProviderOut])
async def list_model_providers(p: BuilderOrAdmin, c: ContainerDep) -> list[ProviderOut]:
    return [_out(v) for v in await c.model_providers.list_providers(p)]


@router.put("/{provider_id}", response_model=ProviderOut)
async def configure_model_provider(
    provider_id: str, body: ConfigureProviderIn, p: AdminOnly, c: ContainerDep
) -> ProviderOut:
    view = await c.model_providers.configure(
        p,
        provider_id,
        enabled=body.enabled,
        base_url=body.base_url,
        api_key=body.api_key,
        models=body.models,
        extra=body.extra,
    )
    return _out(view)


@router.post("/{provider_id}/test", response_model=ProbeOut)
async def test_model_provider(
    provider_id: str, body: TestProviderIn, p: AdminOnly, c: ContainerDep
) -> ProbeOut:
    res = await c.model_providers.test_connection(
        p,
        provider_id,
        base_url=body.base_url,
        api_key=body.api_key,
        extra=body.extra,
    )
    return ProbeOut(ok=res.ok, latency_ms=res.latency_ms, models=res.models, error=res.error)


class CheckModelsIn(TestProviderIn):
    models: list[str] = Field(min_length=1, max_length=100)


class ModelCheckOut(BaseModel):
    model: str
    ok: bool
    error: str | None
    latency_ms: int | None


@router.post("/{provider_id}/check-models", response_model=list[ModelCheckOut])
async def check_models(
    provider_id: str, body: CheckModelsIn, p: AdminOnly, c: ContainerDep
) -> list[ModelCheckOut]:
    """Gọi thử từng model (yêu cầu rất ngắn) để biết model nào thực sự dùng được."""
    checks = await c.model_providers.check_models(
        p,
        provider_id,
        body.models,
        base_url=body.base_url,
        api_key=body.api_key,
        extra=body.extra,
    )
    return [
        ModelCheckOut(model=x.model, ok=x.ok, error=x.error, latency_ms=x.latency_ms)
        for x in checks
    ]


@router.delete("/{provider_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_model_provider(provider_id: str, p: AdminOnly, c: ContainerDep) -> None:
    await c.model_providers.remove(p, provider_id)
