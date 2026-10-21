"""Chooses which model service answers: the local Ollama model or an additional provider."""

from __future__ import annotations

from collections.abc import Callable

from ..config import AppConfig, ProviderSettings
from .ollama_service import OllamaService, OllamaStatus
from .openai_compatible_service import OpenAICompatibleService
from .secret_store import SecretStore


def is_cloud_model_name(name: str) -> bool:
    """Ollama cloud models run on Ollama's servers although Ollama itself is local."""
    lowered = name.lower()
    return lowered.endswith((":cloud", "-cloud")) or ":cloud-" in lowered or "-cloud:" in lowered


class ModelRouter:
    def __init__(self, config_provider: Callable[[], AppConfig], ollama: OllamaService, secrets: SecretStore,
                 factory=OpenAICompatibleService):
        self.config_provider = config_provider
        self.ollama = ollama
        self.secrets = secrets
        self._factory = factory

    def active_provider(self) -> ProviderSettings | None:
        config = self.config_provider()
        return config.provider(config.active_provider) if config.active_provider else None

    def backend(self):
        provider = self.active_provider()
        if provider is None:
            return self.ollama
        return self._factory(provider, self.secrets.get(provider.id), lambda: self.config_provider().ollama)

    def backend_for(self, provider: ProviderSettings, api_key: str | None):
        return self._factory(provider, api_key, lambda: self.config_provider().ollama)

    def model_label(self) -> str:
        provider = self.active_provider()
        if provider is None:
            return self.config_provider().ollama.model
        return f"{provider.model or '(no model)'} via {provider.name}"

    def is_online(self) -> bool:
        provider = self.active_provider()
        if provider is not None:
            return not provider.is_local
        settings = self.config_provider().ollama
        return not settings.is_local or is_cloud_model_name(settings.model)

    def destination(self) -> str:
        """Where messages go, in words."""
        provider = self.active_provider()
        if provider is not None:
            return f"{provider.name} ({provider.host})"
        settings = self.config_provider().ollama
        if is_cloud_model_name(settings.model):
            return "Ollama's cloud service"
        return "Ollama on this computer" if settings.is_local else f"Ollama at {settings.base_url}"

    def status(self) -> OllamaStatus:
        return self.backend().status()
