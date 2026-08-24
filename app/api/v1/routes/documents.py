from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.csrf import require_valid_csrf
from app.auth.dependencies import get_current_active_user, require_founder_control
from app.core.config import get_settings
from app.database.models import User
from app.database.session import get_session
from app.schemas.documents import CustomerDocumentRead, DocumentStatusChange
from app.services.document_storage import DocumentStorageError
from app.services.documents import DocumentAuthorizationError, DocumentError, DocumentService

router = APIRouter(prefix="/documents", tags=["documents"])
SessionDependency = Annotated[AsyncSession, Depends(get_session)]
CurrentUser = Annotated[User, Depends(get_current_active_user)]
FounderUser = Annotated[User, Depends(require_founder_control)]


def _csrf(request: Request) -> None:
    require_valid_csrf(request)


def _error(error: Exception) -> HTTPException:
    if isinstance(error, DocumentAuthorizationError):
        return HTTPException(status_code=404, detail="Document not found")
    if isinstance(error, (DocumentStorageError, DocumentError)):
        return HTTPException(status_code=409, detail=str(error))
    return HTTPException(status_code=500, detail="Document operation failed")


def _read(document) -> CustomerDocumentRead:
    return CustomerDocumentRead.model_validate(document)


@router.post("/founder/customers/{user_id}", response_model=CustomerDocumentRead, status_code=status.HTTP_201_CREATED)
async def upload_founder_document(
    user_id: UUID,
    request: Request,
    actor: FounderUser,
    session: SessionDependency,
    file: Annotated[UploadFile, File(...)],
    title: Annotated[str | None, Form()] = None,
    description: Annotated[str | None, Form()] = None,
    _csrf_guard: Annotated[None, Depends(_csrf)] = None,
) -> CustomerDocumentRead:
    try:
        content = await file.read(get_settings().document_storage_max_bytes + 1)
        document = await DocumentService(session).upload(actor=actor, target_user_id=user_id, filename=file.filename or "", content_type=file.content_type or "", content=content, title=title, description=description)
        return _read(document)
    except Exception as error:
        raise _error(error) from error


@router.get("/founder/customers/{user_id}", response_model=list[CustomerDocumentRead])
async def list_founder_documents(user_id: UUID, actor: FounderUser, session: SessionDependency) -> list[CustomerDocumentRead]:
    try:
        return [_read(item) for item in await DocumentService(session).list_for_founder(actor=actor, target_user_id=user_id)]
    except Exception as error:
        raise _error(error) from error


@router.get("/founder/customers/{user_id}/{document_id}", response_model=CustomerDocumentRead)
async def get_founder_document(user_id: UUID, document_id: UUID, actor: FounderUser, session: SessionDependency) -> CustomerDocumentRead:
    try:
        service = DocumentService(session)
        document = await service.get_for_founder(actor=actor, target_user_id=user_id, document_id=document_id)
        await service.record_access(actor=actor, document=document)
        return _read(document)
    except Exception as error:
        raise _error(error) from error


@router.get("/founder/customers/{user_id}/{document_id}/download")
async def download_founder_document(user_id: UUID, document_id: UUID, actor: FounderUser, session: SessionDependency) -> Response:
    try:
        service = DocumentService(session)
        document = await service.get_for_founder(actor=actor, target_user_id=user_id, document_id=document_id)
        content = await service.storage.get(key=document.storage_key)
        await service.record_access(actor=actor, document=document)
        return Response(content=content, media_type=document.content_type, headers={"Content-Disposition": f'attachment; filename="{document.filename}"'})
    except Exception as error:
        raise _error(error) from error


@router.post("/founder/customers/{user_id}/{document_id}/status", response_model=CustomerDocumentRead)
async def change_founder_document_status(user_id: UUID, document_id: UUID, data: DocumentStatusChange, actor: FounderUser, session: SessionDependency, _csrf_guard: Annotated[None, Depends(_csrf)] = None) -> CustomerDocumentRead:
    try:
        return _read(await DocumentService(session).change_status(actor=actor, target_user_id=user_id, document_id=document_id, data=data))
    except Exception as error:
        raise _error(error) from error


@router.get("", response_model=list[CustomerDocumentRead])
async def list_customer_documents(actor: CurrentUser, session: SessionDependency) -> list[CustomerDocumentRead]:
    try:
        return [_read(item) for item in await DocumentService(session).list_for_customer(actor=actor)]
    except Exception as error:
        raise _error(error) from error


@router.get("/{document_id}", response_model=CustomerDocumentRead)
async def get_customer_document(document_id: UUID, actor: CurrentUser, session: SessionDependency) -> CustomerDocumentRead:
    try:
        service = DocumentService(session)
        document = await service.get_for_customer(actor=actor, document_id=document_id)
        await service.record_access(actor=actor, document=document)
        return _read(document)
    except Exception as error:
        raise _error(error) from error


@router.get("/{document_id}/download")
async def download_customer_document(document_id: UUID, actor: CurrentUser, session: SessionDependency) -> Response:
    try:
        service = DocumentService(session)
        document = await service.get_for_customer(actor=actor, document_id=document_id)
        content = await service.storage.get(key=document.storage_key)
        await service.record_access(actor=actor, document=document)
        return Response(content=content, media_type=document.content_type, headers={"Content-Disposition": f'attachment; filename="{document.filename}"'})
    except Exception as error:
        raise _error(error) from error
