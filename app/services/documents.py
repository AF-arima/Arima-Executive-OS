from __future__ import annotations

from datetime import UTC, datetime
import hashlib
from pathlib import PurePath
import secrets
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.database.models import (
    AuditAction, AuditEntity, CustomerDocument, CustomerDocumentStatus,
    User, Workspace, WorkspaceMembership,
)
from app.schemas.documents import DocumentStatusChange
from app.services.audit import record_audit
from app.services.document_storage import DocumentStorage, get_document_storage


class DocumentError(RuntimeError):
    pass


class DocumentNotConfigured(DocumentError):
    pass


class DocumentValidationError(DocumentError):
    pass


class DocumentAuthorizationError(DocumentError):
    pass


ALLOWED_TYPES = frozenset({
    "application/pdf", "text/plain", "text/csv", "image/png", "image/jpeg",
    "image/gif", "image/webp",
})


def _workspace_query(user_id: UUID):
    return select(Workspace).outerjoin(WorkspaceMembership, WorkspaceMembership.workspace_id == Workspace.id).where(
        or_(Workspace.owner_id == user_id, WorkspaceMembership.user_id == user_id)
    ).order_by(Workspace.created_at)


class DocumentService:
    def __init__(self, session: AsyncSession, *, storage: DocumentStorage | None = None, settings: Settings | None = None) -> None:
        self.session = session
        self.settings = settings or get_settings()
        self.storage = storage or get_document_storage(self.settings)

    async def workspace_for(self, user_id: UUID) -> Workspace:
        workspaces = list((await self.session.scalars(_workspace_query(user_id))).all())
        if len(workspaces) != 1:
            raise DocumentAuthorizationError("Authorized workspace selection is unavailable")
        return workspaces[0]

    async def founder_target(self, actor: User, target_user_id: UUID) -> tuple[User, Workspace]:
        target = await self.session.get(User, target_user_id)
        if target is None:
            raise DocumentAuthorizationError("Customer account not found")
        return target, await self.workspace_for(target.id)

    @staticmethod
    def _validate_filename(filename: str) -> str:
        name = filename.strip()
        if not name or name in {".", ".."} or "/" in name or "\\" in name or '"' in name or PurePath(name).name != name:
            raise DocumentValidationError("Invalid document filename")
        if any(ord(char) < 32 for char in name):
            raise DocumentValidationError("Invalid document filename")
        return name[:255]

    def _validate_content(self, *, content: bytes, content_type: str) -> None:
        if not content:
            raise DocumentValidationError("Document cannot be empty")
        if len(content) > self.settings.document_storage_max_bytes:
            raise DocumentValidationError("Document exceeds the maximum size")
        if content_type not in ALLOWED_TYPES:
            raise DocumentValidationError("Document type is not supported")
        if content_type == "application/pdf" and not content.startswith(b"%PDF-"):
            raise DocumentValidationError("Document content does not match PDF type")
        if content_type == "image/png" and not content.startswith(b"\x89PNG\r\n\x1a\n"):
            raise DocumentValidationError("Document content does not match PNG type")
        if content_type == "image/jpeg" and not content.startswith(b"\xff\xd8\xff"):
            raise DocumentValidationError("Document content does not match JPEG type")
        if content_type == "image/gif" and not content.startswith((b"GIF87a", b"GIF89a")):
            raise DocumentValidationError("Document content does not match GIF type")
        if content_type == "image/webp" and not (content.startswith(b"RIFF") and content[8:12] == b"WEBP"):
            raise DocumentValidationError("Document content does not match WebP type")
        if content_type in {"text/plain", "text/csv"}:
            try:
                content.decode("utf-8")
            except UnicodeDecodeError as error:
                raise DocumentValidationError("Text document is not valid UTF-8") from error
            if b"\x00" in content:
                raise DocumentValidationError("Text document contains binary content")

    async def upload(self, *, actor: User, target_user_id: UUID, filename: str, content_type: str, content: bytes, title: str | None, description: str | None) -> CustomerDocument:
        target, workspace = await self.founder_target(actor, target_user_id)
        name = self._validate_filename(filename)
        normalized_type = content_type.strip().lower()
        self._validate_content(content=content, content_type=normalized_type)
        document = CustomerDocument(
            workspace_id=workspace.id, target_user_id=target.id, uploaded_by_id=actor.id,
            storage_key="pending", filename=name, content_type=normalized_type,
            size_bytes=len(content), checksum_sha256=hashlib.sha256(content).hexdigest(),
            title=(title or name).strip()[:255], description=description.strip()[:2000] if description else None,
            provenance={"source": "founder_upload", "actor_id": str(actor.id)},
            status=CustomerDocumentStatus.ACTIVE.value,
        )
        self.session.add(document)
        await self.session.flush()
        key = f"documents/{workspace.id}/{document.id}/{secrets.token_hex(16)}"
        document.storage_key = key
        try:
            await self.storage.put(key=key, content=content, content_type=normalized_type)
            record_audit(self.session, actor_id=actor.id, action=AuditAction.CREATE, entity=AuditEntity.DOCUMENT, entity_id=document.id, event_type="CUSTOMER_DOCUMENT_UPLOADED", event_metadata={"target_user_id": str(target.id), "workspace_id": str(workspace.id), "content_type": normalized_type, "size_bytes": len(content)})
            await self.session.commit()
        except Exception:
            await self.session.rollback()
            try:
                await self.storage.delete(key=key)
            except Exception:
                pass
            raise
        return document

    async def list_for_founder(self, *, actor: User, target_user_id: UUID) -> list[CustomerDocument]:
        _, workspace = await self.founder_target(actor, target_user_id)
        return list((await self.session.scalars(select(CustomerDocument).where(CustomerDocument.workspace_id == workspace.id, CustomerDocument.target_user_id == target_user_id).order_by(CustomerDocument.created_at.desc()))).all())

    async def list_for_customer(self, *, actor: User) -> list[CustomerDocument]:
        workspace = await self.workspace_for(actor.id)
        return list((await self.session.scalars(select(CustomerDocument).where(CustomerDocument.workspace_id == workspace.id, CustomerDocument.target_user_id == actor.id, CustomerDocument.status == CustomerDocumentStatus.ACTIVE.value).order_by(CustomerDocument.created_at.desc()))).all())

    async def get_for_founder(self, *, actor: User, target_user_id: UUID, document_id: UUID) -> CustomerDocument:
        _, workspace = await self.founder_target(actor, target_user_id)
        document = await self.session.scalar(select(CustomerDocument).where(CustomerDocument.id == document_id, CustomerDocument.workspace_id == workspace.id, CustomerDocument.target_user_id == target_user_id))
        if document is None:
            raise DocumentAuthorizationError("Document not found")
        return document

    async def get_for_customer(self, *, actor: User, document_id: UUID) -> CustomerDocument:
        workspace = await self.workspace_for(actor.id)
        document = await self.session.scalar(select(CustomerDocument).where(CustomerDocument.id == document_id, CustomerDocument.workspace_id == workspace.id, CustomerDocument.target_user_id == actor.id, CustomerDocument.status == CustomerDocumentStatus.ACTIVE.value))
        if document is None:
            raise DocumentAuthorizationError("Document not found")
        return document

    async def change_status(self, *, actor: User, target_user_id: UUID, document_id: UUID, data: DocumentStatusChange) -> CustomerDocument:
        document = await self.get_for_founder(actor=actor, target_user_id=target_user_id, document_id=document_id)
        document.status = data.status
        document.archived_at = datetime.now(UTC)
        record_audit(self.session, actor_id=actor.id, action=AuditAction.STATUS_CHANGE, entity=AuditEntity.DOCUMENT, entity_id=document.id, event_type=f"CUSTOMER_DOCUMENT_{data.status.upper()}", event_metadata={"target_user_id": str(target_user_id), "workspace_id": str(document.workspace_id), "reason": data.reason})
        await self.session.commit()
        return document

    async def record_access(self, *, actor: User, document: CustomerDocument) -> None:
        record_audit(self.session, actor_id=actor.id, action=AuditAction.READ, entity=AuditEntity.DOCUMENT, entity_id=document.id, event_type="CUSTOMER_DOCUMENT_ACCESSED", event_metadata={"target_user_id": str(document.target_user_id), "workspace_id": str(document.workspace_id)})
        await self.session.commit()
