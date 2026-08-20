from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.csrf import require_valid_csrf
from app.auth.dependencies import require_founder_control
from app.database.models import User
from app.database.session import get_session
from app.integrations import microsoft

router = APIRouter(prefix="/integrations/microsoft", tags=["integrations"])
Session = Annotated[AsyncSession, Depends(get_session)]


@router.get("/authorize")
async def authorize(
    request: Request,
    session: Session,
    actor: User = Depends(require_founder_control),
    workspace_id: UUID = Query(...),
):
    try:
        url = await microsoft.authorize_url(session, actor.id, workspace_id)
        if "application/json" in request.headers.get("accept", ""):
            return {"authorization_url": url}
        return RedirectResponse(url)
    except microsoft.MicrosoftIntegrationError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.get("/callback")
async def callback(
    request: Request,
    session: Session,
    state: str | None = None,
    code: str | None = None,
    error: str | None = None,
):
    if error or not state or not code:
        raise HTTPException(status_code=400, detail="Microsoft authorization was not completed")
    try:
        credential = await microsoft.complete_callback(session, state=state, code=code)
    except microsoft.MicrosoftIntegrationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    del request, credential
    return {"status": "authorized", "provider": microsoft.PROVIDER}


@router.get("/status")
async def integration_status(
    session: Session,
    actor: User = Depends(require_founder_control),
    workspace_id: UUID = Query(...),
):
    try:
        return await microsoft.status(session, actor.id, workspace_id)
    except microsoft.MicrosoftIntegrationError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error


@router.post("/disconnect")
async def disconnect(
    request: Request,
    session: Session,
    actor: User = Depends(require_founder_control),
    workspace_id: UUID = Query(...),
):
    require_valid_csrf(request)
    try:
        await microsoft.disconnect(session, actor.id, workspace_id)
    except microsoft.MicrosoftIntegrationError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    return {"status": "disconnected", "provider": microsoft.PROVIDER}
