"""ModelRouter — chọn adapter theo AgentSpec và phân loại dữ liệu (ADR-0012)."""

from __future__ import annotations

from collections.abc import Mapping

from aiagent.modules.agents.domain import AgentSpec, DataClassification
from aiagent.modules.runtime.domain import ChatModelPort, ModelUnavailableError
from aiagent.shared_kernel.errors import ForbiddenError

# Provider chạy tại chỗ, dữ liệu không rời hạ tầng → được dùng cho dữ liệu 'restricted'.
LOCAL_PROVIDERS = frozenset({"fake", "ollama"})


class ModelRouter:
    def __init__(
        self,
        providers: Mapping[str, ChatModelPort],
        local_providers: frozenset[str] = LOCAL_PROVIDERS,
    ) -> None:
        self._providers = dict(providers)
        self._local = local_providers  # provider xử lý dữ liệu tại chỗ (không gửi ra ngoài)

    def select(self, spec: AgentSpec) -> ChatModelPort:
        provider = spec.model.provider
        if (
            spec.data_classification is DataClassification.RESTRICTED
            and provider not in self._local
        ):
            raise ForbiddenError(
                "Agent xử lý dữ liệu 'restricted' chỉ được dùng model tự host, "
                "hiện chưa được cấu hình."
            )
        model = self._providers.get(provider)
        if model is None:
            raise ModelUnavailableError(f"Provider '{provider}' chưa được cấu hình.")
        return model

    @property
    def providers(self) -> list[str]:
        return sorted(self._providers)

    def get(self, provider_id: str) -> ChatModelPort | None:
        return self._providers.get(provider_id)

    def set_provider(self, provider_id: str, model: ChatModelPort) -> None:
        self._providers[provider_id] = model

    def remove_provider(self, provider_id: str) -> None:
        self._providers.pop(provider_id, None)
