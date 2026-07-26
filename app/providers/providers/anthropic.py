from app.providers.base import ConfiguredProviderStub
from app.providers.config import ProviderConfig
from app.providers.exceptions import ProviderConfigurationError
from app.providers.types import ModelInfo, ProviderName


class AnthropicProvider(ConfiguredProviderStub):
    def __init__(self, config: ProviderConfig) -> None:
        if config.provider is not ProviderName.ANTHROPIC:
            raise ProviderConfigurationError(
                "AnthropicProvider requires anthropic configuration"
            )
        super().__init__(
            ModelInfo(
                provider=config.provider,
                model=config.default_model,
                display_name="Anthropic Provider Placeholder",
                context_window=config.max_model_tokens,
                max_output_tokens=config.max_output_tokens,
                capabilities=config.capabilities,
            )
        )
