import asyncio
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.database.models import Base
from app.database.session import get_session
from app.email.base import TransactionalEmailProvider
from app.email.exceptions import EmailProviderError
from app.email.factory import get_transactional_email_service
from app.email.service import TransactionalEmailService
from app.email.types import EmailDelivery, EmailMessage
from app.main import app


@dataclass(frozen=True)
class AuthTestContext:
    client: TestClient
    session_factory: async_sessionmaker[AsyncSession]
    email_provider: "RecordingEmailProvider"


class RecordingEmailProvider(TransactionalEmailProvider):
    def __init__(self) -> None:
        self.messages: list[EmailMessage] = []
        self.fail_next_delivery = False

    @property
    def provider_name(self) -> str:
        return "recording"

    async def send(self, message: EmailMessage) -> EmailDelivery:
        if self.fail_next_delivery:
            self.fail_next_delivery = False
            raise EmailProviderError("recording delivery failure")
        self.messages.append(message)
        return EmailDelivery(
            provider=self.provider_name,
            message_id=str(len(self.messages)),
        )


@pytest.fixture
def auth_context(tmp_path: Path) -> Iterator[AuthTestContext]:
    database_path = tmp_path / "auth.sqlite3"
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{database_path}"
    )
    session_factory = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    async def create_schema() -> None:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    async def override_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    asyncio.run(create_schema())
    email_provider = RecordingEmailProvider()
    email_service = TransactionalEmailService(email_provider)
    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_transactional_email_service] = lambda: email_service
    try:
        with TestClient(app) as client:
            yield AuthTestContext(client, session_factory, email_provider)
    finally:
        app.dependency_overrides.clear()
        asyncio.run(engine.dispose())
