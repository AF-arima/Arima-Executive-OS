from abc import ABC, abstractmethod

from app.email.types import EmailDelivery, EmailMessage


class TransactionalEmailProvider(ABC):
    @property
    @abstractmethod
    def provider_name(self) -> str:
        raise NotImplementedError

    @abstractmethod
    async def send(self, message: EmailMessage) -> EmailDelivery:
        raise NotImplementedError
