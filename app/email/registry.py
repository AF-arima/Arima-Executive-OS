from collections.abc import Callable

from app.email.base import TransactionalEmailProvider

ProviderBuilder = Callable[[], TransactionalEmailProvider]


class TransactionalEmailProviderRegistry:
    def __init__(self) -> None:
        self._builders: dict[str, ProviderBuilder] = {}

    def register(self, name: str, builder: ProviderBuilder) -> None:
        normalized = name.strip().lower()
        if not normalized:
            raise ValueError("Provider name cannot be blank")
        self._builders[normalized] = builder

    def create(self, name: str) -> TransactionalEmailProvider:
        try:
            return self._builders[name.strip().lower()]()
        except KeyError as error:
            raise KeyError(f"Unknown transactional email provider: {name}") from error


registry = TransactionalEmailProviderRegistry()
