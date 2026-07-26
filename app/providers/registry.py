from collections.abc import Iterable

from app.providers.base import ProviderAdapter
from app.providers.exceptions import (
    InvalidModel,
    ProviderConfigurationError,
    ProviderUnavailable,
)
from app.providers.types import (
    ProviderCapability,
    ProviderName,
)


class ProviderRegistry:
    def __init__(
        self,
        providers: Iterable[ProviderAdapter] = (),
    ) -> None:
        self._providers: dict[ProviderName, ProviderAdapter] = {}
        for provider in providers:
            self.register(provider)

    def register(
        self,
        provider: ProviderAdapter,
        *,
        replace: bool = False,
    ) -> None:
        if provider.provider in self._providers and not replace:
            raise ProviderConfigurationError(
                f"Provider already registered: {provider.provider.value}"
            )
        for model in provider.models:
            information = provider.model_information(model)
            if information.provider is not provider.provider:
                raise ProviderConfigurationError(
                    "Model metadata provider does not match adapter"
                )
        self._providers[provider.provider] = provider

    def unregister(self, provider: ProviderName | str) -> None:
        name = ProviderName(provider)
        if self._providers.pop(name, None) is None:
            raise ProviderUnavailable(
                f"Provider is not registered: {name.value}"
            )

    def get(
        self,
        provider: ProviderName | str,
        *,
        model: str | None = None,
    ) -> ProviderAdapter:
        name = ProviderName(provider)
        adapter = self._providers.get(name)
        if adapter is None:
            raise ProviderUnavailable(
                f"Provider is not registered: {name.value}"
            )
        if model is not None and model not in adapter.models:
            raise InvalidModel(
                f"Model {model!r} is not registered for {name.value}"
            )
        return adapter

    def find(
        self,
        *,
        provider: ProviderName | str | None = None,
        model: str | None = None,
        capabilities: frozenset[ProviderCapability] = frozenset(),
    ) -> tuple[ProviderAdapter, ...]:
        provider_name = ProviderName(provider) if provider is not None else None
        matches: list[ProviderAdapter] = []
        for name, adapter in sorted(
            self._providers.items(),
            key=lambda item: item[0].value,
        ):
            if provider_name is not None and name is not provider_name:
                continue
            candidate_models = (
                (model,) if model is not None else adapter.models
            )
            for candidate_model in candidate_models:
                if candidate_model not in adapter.models:
                    continue
                information = adapter.model_information(candidate_model)
                if information.capabilities.supports(capabilities):
                    matches.append(adapter)
                    break
        return tuple(matches)

    def resolve(
        self,
        *,
        provider: ProviderName | str | None = None,
        model: str | None = None,
        capabilities: frozenset[ProviderCapability] = frozenset(),
    ) -> ProviderAdapter:
        matches = self.find(
            provider=provider,
            model=model,
            capabilities=capabilities,
        )
        if not matches:
            raise ProviderUnavailable(
                "No registered provider matches the requested model "
                "and capabilities"
            )
        return matches[0]

    def list(self) -> tuple[ProviderAdapter, ...]:
        return tuple(
            self._providers[name]
            for name in sorted(self._providers, key=lambda item: item.value)
        )
