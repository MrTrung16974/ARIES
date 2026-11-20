from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Cấu hình đọc từ biến môi trường tiền tố `AIAGENT_` (xem deploy/.env.example)."""

    model_config = SettingsConfigDict(env_prefix="AIAGENT_", env_file=".env", extra="ignore")

    env: Literal["dev", "test", "staging", "prod"] = "dev"
    app_name: str = "AI Agent Platform"
    log_level: str = "INFO"
    log_json: bool = True

    # Kết nối bằng role ứng dụng (không phải owner) để RLS luôn có hiệu lực.
    database_url: SecretStr = SecretStr(
        "postgresql+asyncpg://aiagent_app:aiagent_app@localhost:5432/aiagent"
    )
    # Role owner, chỉ dùng cho Alembic.
    migrator_database_url: SecretStr | None = None
    db_pool_size: int = 10

    redis_url: SecretStr = SecretStr("redis://localhost:6379/0")

    oidc_issuer: str = "http://localhost:8080/realms/aiagent"
    oidc_jwks_url: str | None = None
    oidc_audience: str = "aiagent-api"
    oidc_tenant_claim: str = "tenant_id"
    oidc_leeway_seconds: int = Field(default=30, ge=0, le=300)

    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:4200"])
    plugins_disabled: list[str] = Field(default_factory=list)

    # LLM (ADR-0012). Key nằm ngoài source; Phase 3 chuyển sang OpenBao.
    anthropic_api_key: SecretStr | None = None
    anthropic_base_url: str | None = None
    # Model giả "fake-echo" cho dev/demo không cần key. Không bật ở prod.
    llm_fake_enabled: bool = False
    tool_timeout_seconds: float = Field(default=30.0, gt=0, le=300)

    # Agent Architect (docs/agent-architect.md). auto: Claude nếu có key, heuristic nếu bật model giả.
    architect_brain: Literal["auto", "llm", "heuristic"] = "auto"
    architect_model: str = "claude-opus-5-5"
    architect_fast_model: str = "claude-haiku-5-5"
    architect_judge_model: str = "claude-sonnet-5-5"

    @property
    def jwks_url(self) -> str:
        return self.oidc_jwks_url or f"{self.oidc_issuer}/protocol/openid-connect/certs"

    @property
    def is_prod(self) -> bool:
        return self.env == "prod"

    @model_validator(mode="after")
    def _check_prod(self) -> Settings:
        if self.is_prod:
            if not self.oidc_issuer.startswith("https://"):
                raise ValueError("Môi trường prod bắt buộc OIDC issuer dùng HTTPS")
            if any(o == "*" for o in self.cors_origins):
                raise ValueError("Không cho phép CORS '*' ở prod")
            if self.llm_fake_enabled:
                raise ValueError("Không bật model giả ở prod")
            if self.architect_brain == "heuristic":
                raise ValueError("Không dùng Architect heuristic ở prod")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
