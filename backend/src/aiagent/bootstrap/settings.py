from __future__ import annotations

import sys
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class OAuthProviderSettings(BaseModel):
    """Nhà cung cấp OAuth cho connector (ADR-0018). client_secret không log, không trả ra API."""

    name: str
    authorize_url: str
    token_url: str
    userinfo_url: str | None = None
    client_id: str
    client_secret: SecretStr
    scopes: list[str] = Field(default_factory=list)
    extra_params: dict[str, str] = Field(default_factory=dict)


_GOOGLE = {
    "name": "Google",
    "authorize_url": "https://accounts.google.com/o/oauth2/v2/auth",
    "token_url": "https://oauth2.googleapis.com/token",
    "userinfo_url": "https://openidconnect.googleapis.com/v1/userinfo",
    "scopes": ["openid", "email"],
    # Cần refresh token để dùng lâu dài.
    "extra_params": {"access_type": "offline", "prompt": "consent"},
}


class Settings(BaseSettings):
    """Cấu hình đọc từ biến môi trường tiền tố `AIAGENT_` (xem deploy/.env.example)."""

    model_config = SettingsConfigDict(env_prefix="AIAGENT_", env_file=".env", extra="ignore")

    env: Literal["dev", "test", "staging", "prod"] = "dev"
    app_name: str = "ARIES"
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
    # Cấu hình KHÔNG bí mật theo plugin, ví dụ {"web": {"searxng_url": "http://searxng:8080"}}.
    plugin_config: dict[str, dict[str, Any]] = Field(default_factory=dict)
    # MCP stdio (ADR-0018): tên lệnh → dòng lệnh, do NGƯỜI VẬN HÀNH cài/cho phép. UI chỉ chọn tên.
    # URL công khai của ARIES (trình duyệt thấy) — dùng cho redirect_uri của OAuth.
    public_base_url: str = "http://localhost"
    # Nhà cung cấp OAuth: {"keycloak": {...}}. Google chỉ cần client id/secret bên dưới.
    oauth_providers: dict[str, OAuthProviderSettings] = Field(default_factory=dict)
    oauth_google_client_id: str | None = None
    oauth_google_client_secret: SecretStr | None = None

    mcp_stdio_commands: dict[str, list[str]] = Field(
        default_factory=lambda: {"aries_demo": [sys.executable, "-m", "aiagent_plugins.mcp_demo"]}
    )

    # LLM (ADR-0012). Key nằm ngoài source; Phase 3 chuyển sang OpenBao.
    anthropic_api_key: SecretStr | None = None
    anthropic_base_url: str | None = None
    # Model giả "fake-echo" cho dev/demo không cần key. Không bật ở prod.
    llm_fake_enabled: bool = False
    tool_timeout_seconds: float = Field(default=30.0, gt=0, le=300)

    # Kho bí mật OpenBao (KV v2) cho secret của connection (ADR-0016). Dev: root token;
    # Phase 3b: AppRole.
    openbao_addr: str = "http://localhost:8200"
    openbao_token: SecretStr | None = None
    # AppRole (ADR-0018): file do openbao-init cấp, đọc lại mỗi lần đăng nhập.
    openbao_role_id_file: Path | None = None
    openbao_secret_id_file: Path | None = None
    openbao_kv_mount: str = "secret"

    # Agent Architect (docs/agent-architect.md). auto: Claude nếu có key, heuristic nếu bật model giả.
    architect_brain: Literal["auto", "llm", "heuristic"] = "auto"
    architect_model: str = "claude-opus-5-5"
    architect_fast_model: str = "claude-haiku-5-5"
    architect_judge_model: str = "claude-sonnet-5-5"

    def all_oauth_providers(self) -> dict[str, OAuthProviderSettings]:
        providers = {
            k: v for k, v in self.oauth_providers.items() if v.client_secret.get_secret_value()
        }
        secret = self.oauth_google_client_secret
        if self.oauth_google_client_id and secret and secret.get_secret_value():
            providers.setdefault(
                "google",
                OAuthProviderSettings.model_validate(
                    {**_GOOGLE, "client_id": self.oauth_google_client_id, "client_secret": secret}
                ),
            )
        return providers

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
