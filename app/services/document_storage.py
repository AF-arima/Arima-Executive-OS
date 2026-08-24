from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Protocol

from app.core.config import Settings, get_settings


class DocumentStorageError(RuntimeError):
    pass


class DocumentStorageNotConfigured(DocumentStorageError):
    pass


class DocumentStorage(Protocol):
    async def put(self, *, key: str, content: bytes, content_type: str) -> None: ...
    async def get(self, *, key: str) -> bytes: ...
    async def delete(self, *, key: str) -> None: ...


@dataclass(frozen=True, slots=True)
class StoredObject:
    content: bytes
    content_type: str


class InMemoryDocumentStorage:
    def __init__(self) -> None:
        self.objects: dict[str, StoredObject] = {}

    async def put(self, *, key: str, content: bytes, content_type: str) -> None:
        self.objects[key] = StoredObject(content, content_type)

    async def get(self, *, key: str) -> bytes:
        try:
            return self.objects[key].content
        except KeyError as error:
            raise DocumentStorageError("Document object not found") from error

    async def delete(self, *, key: str) -> None:
        self.objects.pop(key, None)


class NotConfiguredDocumentStorage:
    async def put(self, *, key: str, content: bytes, content_type: str) -> None:
        del key, content, content_type
        raise DocumentStorageNotConfigured("Document storage is not configured")

    async def get(self, *, key: str) -> bytes:
        del key
        raise DocumentStorageNotConfigured("Document storage is not configured")

    async def delete(self, *, key: str) -> None:
        del key
        raise DocumentStorageNotConfigured("Document storage is not configured")


class R2DocumentStorage:
    def __init__(self, settings: Settings) -> None:
        if not settings.r2_endpoint_url or not settings.r2_bucket or not settings.r2_access_key_id or not settings.r2_secret_access_key:
            raise DocumentStorageNotConfigured("Document storage is not configured")
        try:
            import boto3  # type: ignore[import-not-found]
        except ImportError as error:
            raise DocumentStorageNotConfigured("R2 storage client is unavailable") from error
        self.bucket = settings.r2_bucket
        self.client = boto3.client(
            "s3",
            endpoint_url=settings.r2_endpoint_url,
            aws_access_key_id=settings.r2_access_key_id,
            aws_secret_access_key=settings.r2_secret_access_key.get_secret_value(),
            region_name="auto",
        )

    async def put(self, *, key: str, content: bytes, content_type: str) -> None:
        await asyncio.to_thread(self.client.put_object, Bucket=self.bucket, Key=key, Body=content, ContentType=content_type)

    async def get(self, *, key: str) -> bytes:
        result = await asyncio.to_thread(self.client.get_object, Bucket=self.bucket, Key=key)
        return await asyncio.to_thread(result["Body"].read)

    async def delete(self, *, key: str) -> None:
        await asyncio.to_thread(self.client.delete_object, Bucket=self.bucket, Key=key)


def get_document_storage(settings: Settings | None = None) -> DocumentStorage:
    active = settings or get_settings()
    if not all((active.r2_endpoint_url, active.r2_bucket, active.r2_access_key_id, active.r2_secret_access_key)):
        return NotConfiguredDocumentStorage()
    return R2DocumentStorage(active)
