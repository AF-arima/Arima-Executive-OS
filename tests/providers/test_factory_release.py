from app.core.config import Settings
from app.providers.factory import ProviderFactory


def test_disabled_ai_builds_empty_provider_registry() -> None:
    settings = Settings(_env_file=None, ai_execution_enabled=False)

    registry = ProviderFactory(settings=settings).build_registry()

    assert registry.list() == ()


def test_enabled_ai_registers_only_the_selected_provider() -> None:
    settings = Settings(_env_file=None)

    registry = ProviderFactory(settings=settings).build_registry()

    assert tuple(adapter.provider.value for adapter in registry.list()) == (
        "mock",
    )
