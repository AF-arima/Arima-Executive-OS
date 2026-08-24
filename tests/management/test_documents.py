import asyncio
from uuid import uuid4
import pytest
from sqlalchemy import select

from app.database.models import AuditEntity, AuditLog, CustomerDocument
from app.services.document_storage import InMemoryDocumentStorage
from tests.auth.helpers import bearer, csrf_headers, grant_role, login_user, register_user


@pytest.fixture
def founder_allowlist(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("FOUNDER_CONTROL_EMAILS", "founder@example.com")
    from app.core.config import get_settings
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def fake_document_storage(monkeypatch: pytest.MonkeyPatch) -> InMemoryDocumentStorage:
    storage = InMemoryDocumentStorage()
    monkeypatch.setattr("app.services.documents.get_document_storage", lambda settings: storage)
    return storage


def test_founder_document_upload_is_scoped_audited_and_retrievable(
    management_context, founder_allowlist, fake_document_storage: InMemoryDocumentStorage,
) -> None:
    register_user(management_context, "founder@example.com")
    customer = register_user(management_context, "customer@example.com")
    grant_role(management_context, "founder@example.com", "administrator")
    founder_headers = bearer(login_user(management_context, "founder@example.com")["access_token"])
    endpoint = f"/api/v1/documents/founder/customers/{customer['id']}"

    missing_csrf = management_context.client.post(endpoint, headers=founder_headers, files={"file": ("evidence.txt", b"customer evidence", "text/plain")})
    response = management_context.client.post(endpoint, headers={**founder_headers, **csrf_headers(management_context)}, files={"file": ("evidence.txt", b"customer evidence", "text/plain")}, data={"title": "Evidence"})

    assert missing_csrf.status_code == 403
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["target_user_id"] == customer["id"]
    assert body["filename"] == "evidence.txt"
    assert "storage_key" not in body
    assert len(fake_document_storage.objects) == 1

    document_id = body["id"]
    download = management_context.client.get(f"{endpoint}/{document_id}/download", headers=founder_headers)
    assert download.status_code == 200
    assert download.content == b"customer evidence"
    assert download.headers["content-type"] == "text/plain; charset=utf-8"

    async def persisted() -> tuple[CustomerDocument | None, list[AuditLog]]:
        async with management_context.session_factory() as session:
            return await session.scalar(select(CustomerDocument)), list((await session.scalars(select(AuditLog).where(AuditLog.entity == AuditEntity.DOCUMENT))).all())

    document, audit = asyncio.run(persisted())
    assert document is not None
    assert audit
    assert {item.event_type for item in audit} >= {"CUSTOMER_DOCUMENT_UPLOADED", "CUSTOMER_DOCUMENT_ACCESSED"}
    assert all(item.entity_id == document.id for item in audit)


def test_customer_document_access_is_tenant_scoped_and_founder_only(
    management_context, founder_allowlist, fake_document_storage: InMemoryDocumentStorage,
) -> None:
    register_user(management_context, "founder@example.com")
    customer_a = register_user(management_context, "customer-a@example.com")
    register_user(management_context, "customer-b@example.com")
    register_user(management_context, "other-admin@example.com")
    grant_role(management_context, "founder@example.com", "administrator")
    grant_role(management_context, "other-admin@example.com", "administrator")
    founder_headers = bearer(login_user(management_context, "founder@example.com")["access_token"])
    customer_a_headers = bearer(login_user(management_context, "customer-a@example.com")["access_token"])
    other_admin_headers = bearer(login_user(management_context, "other-admin@example.com")["access_token"])
    customer_b_headers = bearer(login_user(management_context, "customer-b@example.com")["access_token"])

    endpoint = f"/api/v1/documents/founder/customers/{customer_a['id']}"
    created = management_context.client.post(endpoint, headers={**founder_headers, **csrf_headers(management_context)}, files={"file": ("a.csv", b"asset,amount\nETH,1\n", "text/csv")})
    assert created.status_code == 201
    document_id = created.json()["id"]

    assert management_context.client.post(endpoint, headers={**customer_a_headers, **csrf_headers(management_context)}, files={"file": ("x.txt", b"x", "text/plain")}).status_code == 403
    assert management_context.client.post(endpoint, headers={**other_admin_headers, **csrf_headers(management_context)}, files={"file": ("x.txt", b"x", "text/plain")}).status_code == 403
    assert management_context.client.post(f"/api/v1/documents/founder/customers/{uuid4()}", headers={**founder_headers, **csrf_headers(management_context)}, files={"file": ("x.txt", b"x", "text/plain")}).status_code == 404
    assert management_context.client.get("/api/v1/documents", headers=customer_a_headers).json()[0]["id"] == document_id
    assert management_context.client.get("/api/v1/documents", headers=customer_b_headers).json() == []
    assert management_context.client.get(f"/api/v1/documents/{document_id}", headers=customer_b_headers).status_code == 404

    archived = management_context.client.post(f"{endpoint}/{document_id}/status", headers={**founder_headers, **csrf_headers(management_context)}, json={"status": "archived", "reason": "superseded"})
    assert archived.status_code == 200
    assert management_context.client.get("/api/v1/documents", headers=customer_a_headers).json() == []


def test_storage_not_configured_fails_closed_without_database_metadata(
    management_context, founder_allowlist, monkeypatch: pytest.MonkeyPatch,
) -> None:
    register_user(management_context, "founder@example.com")
    customer = register_user(management_context, "customer@example.com")
    grant_role(management_context, "founder@example.com", "administrator")
    monkeypatch.setattr("app.services.documents.get_document_storage", lambda settings: __import__("app.services.document_storage", fromlist=["NotConfiguredDocumentStorage"]).NotConfiguredDocumentStorage())
    headers = bearer(login_user(management_context, "founder@example.com")["access_token"])
    response = management_context.client.post(f"/api/v1/documents/founder/customers/{customer['id']}", headers={**headers, **csrf_headers(management_context)}, files={"file": ("x.txt", b"x", "text/plain")})
    assert response.status_code == 409

    async def count() -> int:
        async with management_context.session_factory() as session:
            return len((await session.scalars(select(CustomerDocument))).all())

    assert asyncio.run(count()) == 0


def test_document_validation_and_storage_failure_leave_no_metadata(
    management_context, founder_allowlist, monkeypatch: pytest.MonkeyPatch,
) -> None:
    register_user(management_context, "founder@example.com")
    customer = register_user(management_context, "customer@example.com")
    grant_role(management_context, "founder@example.com", "administrator")
    headers = bearer(login_user(management_context, "founder@example.com")["access_token"])
    endpoint = f"/api/v1/documents/founder/customers/{customer['id']}"
    request_headers = {**headers, **csrf_headers(management_context)}

    malformed_pdf = management_context.client.post(endpoint, headers=request_headers, files={"file": ("bad.pdf", b"not a pdf", "application/pdf")})
    unsupported = management_context.client.post(endpoint, headers=request_headers, files={"file": ("bad.exe", b"MZ", "application/octet-stream")})
    assert malformed_pdf.status_code == 409
    assert unsupported.status_code == 409

    class FailingStorage:
        async def put(self, **kwargs):
            raise RuntimeError("storage unavailable")

        async def delete(self, **kwargs):
            return None

    monkeypatch.setattr("app.services.documents.get_document_storage", lambda settings: FailingStorage())
    failed = management_context.client.post(endpoint, headers=request_headers, files={"file": ("ok.txt", b"valid", "text/plain")})
    assert failed.status_code == 500

    async def count() -> int:
        async with management_context.session_factory() as session:
            return len((await session.scalars(select(CustomerDocument))).all())

    assert asyncio.run(count()) == 0
