import pytest
from sqlalchemy import select

from app.auth.exceptions import InvalidMFACodeError, MFAAlreadyEnabledError
from app.auth.passwords import hash_password
from app.auth.service import AuthenticationService
from app.auth.totp import code_for, current_step, decrypt_secret
from app.database.models import AuditLog, User
from app.services.exceptions import PermissionDeniedError
from tests.database.helpers import sqlite_session


@pytest.mark.asyncio
async def test_mfa_enrollment_encrypts_secret_and_accepts_one_code_once():
    async with sqlite_session() as session:
        user = User(
            email="mfa@example.com",
            hashed_password=hash_password("Current-Password-123!"),
            first_name="MFA",
            last_name="User",
            is_verified=True,
        )
        session.add(user)
        await session.flush()
        service = AuthenticationService(session)

        secret, uri = await service.begin_mfa_enrollment(user)
        assert "secret=" in uri
        assert user.mfa_secret_encrypted is not None
        assert secret not in user.mfa_secret_encrypted
        assert decrypt_secret(user.mfa_secret_encrypted) == secret

        code = code_for(secret, current_step())
        await service.confirm_mfa_enrollment(user, code)
        assert user.mfa_enabled is True
        assert user.mfa_last_accepted_step is not None

        with pytest.raises(InvalidMFACodeError):
            await service._verify_login_mfa(user, code)

        with pytest.raises(MFAAlreadyEnabledError):
            await service.begin_mfa_enrollment(user)


@pytest.mark.asyncio
async def test_mfa_rejects_expired_or_malformed_code_without_secret_exposure():
    async with sqlite_session() as session:
        user = User(
            email="mfa-expired@example.com",
            hashed_password=hash_password("Current-Password-123!"),
            first_name="MFA",
            last_name="User",
            is_verified=True,
        )
        session.add(user)
        await session.flush()
        service = AuthenticationService(session)
        secret, _ = await service.begin_mfa_enrollment(user)
        user.mfa_enabled = True
        await session.commit()

        with pytest.raises(InvalidMFACodeError):
            await service._verify_login_mfa(user, "000000")
        assert secret not in str(user.__dict__)


@pytest.mark.asyncio
async def test_mfa_recovery_is_not_self_service_and_revokes_target_factor():
    async with sqlite_session() as session:
        actor = User(
            email="mfa-operator@example.com",
            hashed_password=hash_password("Current-Password-123!"),
            first_name="Operator",
            last_name="User",
            is_verified=True,
        )
        target = User(
            email="mfa-target@example.com",
            hashed_password=hash_password("Current-Password-123!"),
            first_name="Target",
            last_name="User",
            is_verified=True,
        )
        session.add_all([actor, target])
        await session.flush()
        service = AuthenticationService(session)
        _, _ = await service.begin_mfa_enrollment(target)
        target.mfa_enabled = True
        await session.commit()

        with pytest.raises(PermissionDeniedError):
            await service.recover_mfa(
                target,
                target.id,
                reason="operator recovery",
            )

        await service.recover_mfa(
            actor,
            target.id,
            reason="verified support recovery",
        )
        assert target.mfa_enabled is False
        assert target.mfa_secret_encrypted is None
        audit = await session.scalar(
            select(AuditLog).where(AuditLog.event_type == "MFA_RECOVERY")
        )
        assert audit is not None
        assert audit.actor_id == actor.id
        assert audit.entity_id == target.id
        assert audit.event_metadata["reason_recorded"] is True
