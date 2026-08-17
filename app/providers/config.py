from __future__ import annotations

from dataclasses import dataclass

from pydantic import ConfigDict, Field, SecretStr
from pydantic.dataclasses import dataclass as pydantic_dataclass

from app.core.config import Settings
from app.providers.types import ProviderCapabilities, ProviderName


@pydantic_dataclass(config=ConfigDict(extra="forbid", frozen=True))
class ProviderConfig:
    provider: ProviderName
    default_model: str = Field(min_length=1, max_length=200)
    max_model_tokens: int = Field(ge=1)
    default_temperature: float = Field(ge=0, le=2)
    max_output_tokens: int = Field(ge=1)
    api_key: SecretStr | None = None
    base_url: str | None = None
    capabilities: ProviderCapabilities = ProviderCapabilities()

    def __post_init__(self) -> None:
        if self.max_output_tokens > self.max_model_tokens:
            raise ValueError(
                "Provider output limit cannot exceed model token limit"
            )


@dataclass(frozen=True, slots=True)
class ProviderPlatformConfig:
    default_provider: ProviderName
    default_model: str
    providers: dict[ProviderName, ProviderConfig]

    def for_provider(self, provider: ProviderName) -> ProviderConfig:
        return self.providers[provider]

    @classmethod
    def from_settings(cls, settings: Settings) -> ProviderPlatformConfig:
        def build(
            provider: ProviderName,
            *,
            model: str | None = None,
            api_key: SecretStr | None = None,
            base_url: str | None = None,
            capabilities: ProviderCapabilities = ProviderCapabilities(),
        ) -> ProviderConfig:
            return ProviderConfig(
                provider=provider,
                default_model=model or settings.default_model,
                max_model_tokens=settings.max_model_tokens,
                default_temperature=settings.default_temperature,
                max_output_tokens=settings.max_output_tokens,
                api_key=api_key,
                base_url=base_url,
                capabilities=capabilities,
            )

        providers = {
            ProviderName.MOCK: build(
                ProviderName.MOCK,
                capabilities=ProviderCapabilities(
                    streaming=True,
                    vision=True,
                    json_mode=True,
                    tools=True,
                    reasoning=True,
                    embeddings=True,
                    multimodal=True,
                )
            ),
            ProviderName.OPENAI: build(
                ProviderName.OPENAI,
                api_key=settings.openai_api_key,
                capabilities=ProviderCapabilities(
                    streaming=True,
                    reasoning=True,
                ),
            ),
            ProviderName.ANTHROPIC: build(
                ProviderName.ANTHROPIC,
                api_key=settings.anthropic_api_key,
            ),
            ProviderName.GEMINI: build(
                ProviderName.GEMINI,
                model=settings.gemini_model,
                api_key=settings.gemini_api_key,
                capabilities=ProviderCapabilities(
                    streaming=True,
                    reasoning=True,
                ),
            ),
            ProviderName.NVIDIA: build(
                ProviderName.NVIDIA,
                api_key=settings.nvidia_api_key,
                capabilities=ProviderCapabilities(
                    streaming=True,
                    reasoning=True,
                ),
            ),
            ProviderName.OLLAMA: build(
                ProviderName.OLLAMA,
                base_url=settings.ollama_url,
            ),
        }
        return cls(
            default_provider=ProviderName(settings.default_provider),
            default_model=settings.default_model,
            providers=providers,
        )
