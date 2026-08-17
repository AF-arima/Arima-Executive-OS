from collections.abc import Callable, Mapping

from app.core.config import Settings, get_settings
from app.providers.base import ProviderAdapter
from app.providers.config import ProviderConfig, ProviderPlatformConfig
from app.providers.exceptions import (
    InvalidModel,
    ProviderConfigurationError,
)
from app.providers.providers import (
    AnthropicProvider,
    GeminiProvider,
    MockProvider,
    NvidiaProvider,
    OllamaProvider,
    OpenAIProvider,
)
from app.providers.registry import ProviderRegistry
from app.providers.types import ProviderName

ProviderBuilder = Callable[[ProviderConfig], ProviderAdapter]


class ProviderFactory:
    def __init__(
        self,
        *,
        settings: Settings | None = None,
        registry: ProviderRegistry | None = None,
        builders: Mapping[ProviderName, ProviderBuilder] | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.config = ProviderPlatformConfig.from_settings(self.settings)
        self.registry = registry or ProviderRegistry()
        self.builders: dict[ProviderName, ProviderBuilder] = {
            ProviderName.MOCK: MockProvider,
            ProviderName.OPENAI: OpenAIProvider,
            ProviderName.ANTHROPIC: AnthropicProvider,
            ProviderName.GEMINI: GeminiProvider,
            ProviderName.NVIDIA: NvidiaProvider,
            ProviderName.OLLAMA: OllamaProvider,
        }
        if builders:
            self.builders.update(builders)

    def create(
        self,
        *,
        provider: ProviderName | str | None = None,
        model: str | None = None,
    ) -> ProviderAdapter:
        provider_name = (
            ProviderName(provider)
            if provider is not None
            else self.config.default_provider
        )
        config = self.config.providers.get(provider_name)
        builder = self.builders.get(provider_name)
        if config is None or builder is None:
            raise ProviderConfigurationError(
                f"No provider builder configured for {provider_name.value}"
            )
        requested_model = model or config.default_model
        if requested_model != config.default_model:
            raise InvalidModel(
                f"Model {requested_model!r} is not configured for "
                f"{provider_name.value}"
            )
        return builder(config)

    def create_and_register(
        self,
        *,
        provider: ProviderName | str | None = None,
        model: str | None = None,
        replace: bool = False,
    ) -> ProviderAdapter:
        adapter = self.create(provider=provider, model=model)
        self.registry.register(adapter, replace=replace)
        return adapter

    def build_registry(
        self,
        providers: tuple[ProviderName, ...] | None = None,
    ) -> ProviderRegistry:
        if not self.settings.ai_execution_enabled:
            return self.registry
        selected = providers or (self.config.default_provider,)
        for provider in selected:
            self.create_and_register(provider=provider, replace=True)
        return self.registry
