from datetime import timedelta
from uuid import uuid4

import jwt
import pytest

from app.auth.exceptions import InvalidTokenError
from app.auth.tokens import JWTService
from app.core.config import Settings


@pytest.fixture
def jwt_service() -> JWTService:
    return JWTService(
        Settings(
            jwt_secret_key="test-only-secret-key-with-at-least-32-characters"
        )
    )


def test_access_token_contains_required_claims(
    jwt_service: JWTService,
) -> None:
    subject = uuid4()
    token = jwt_service.create_access_token(subject)

    claims = jwt_service.decode_token(
        token.value,
        expected_type="access",
    )

    assert claims.subject == subject
    assert claims.token_type == "access"
    assert claims.jti == token.claims.jti
    assert claims.expires_at > claims.issued_at


def test_refresh_token_cannot_be_used_as_access_token(
    jwt_service: JWTService,
) -> None:
    token = jwt_service.create_refresh_token(uuid4())

    with pytest.raises(InvalidTokenError):
        jwt_service.decode_token(token.value, expected_type="access")


def test_expired_token_is_rejected(jwt_service: JWTService) -> None:
    token = jwt_service.create_access_token(
        uuid4(),
        expires_delta=timedelta(seconds=-1),
    )

    with pytest.raises(InvalidTokenError):
        jwt_service.decode_token(token.value, expected_type="access")


def test_malformed_token_is_rejected(jwt_service: JWTService) -> None:
    with pytest.raises(InvalidTokenError):
        jwt_service.decode_token(
            "not-a-jwt",
            expected_type="access",
        )


def test_tokens_have_unique_identifiers(jwt_service: JWTService) -> None:
    subject = uuid4()

    first = jwt_service.create_refresh_token(subject)
    second = jwt_service.create_refresh_token(subject)

    assert first.claims.jti != second.claims.jti
    assert first.value != second.value


def test_invalid_subject_is_rejected(jwt_service: JWTService) -> None:
    valid = jwt_service.create_access_token(uuid4())
    payload = jwt.decode(
        valid.value,
        jwt_service.settings.jwt_secret_key.get_secret_value(),
        algorithms=[jwt_service.settings.jwt_algorithm],
    )
    payload["sub"] = "not-a-uuid"
    invalid = jwt.encode(
        payload,
        jwt_service.settings.jwt_secret_key.get_secret_value(),
        algorithm=jwt_service.settings.jwt_algorithm,
    )

    with pytest.raises(InvalidTokenError):
        jwt_service.decode_token(invalid, expected_type="access")
