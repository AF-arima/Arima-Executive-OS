from app.providers.base import ConfiguredProviderStub
from app.providers.config import ProviderConfig
from app.providers.exceptions import ProviderConfigurationError
from app.providers.types import ModelInfo, ProviderName


class NvidiaProvider(ConfiguredProviderStub):
    def __init__(self, config: ProviderConfig) -> None:
        if config.provider is not ProviderName.NVIDIA:
            raise ProviderConfigurationError(
                "NvidiaProvider requires nvidia configuration"
            )
        super().__init__(
            ModelInfo(
                provider=config.provider,
                model=config.default_model,
                display_name="NVIDIA Provider Placeholder",
                context_window=config.max_model_tokens,
                max_output_tokens=config.max_output_tokens,
                capabilities=config.capabilities,
            )
        )
