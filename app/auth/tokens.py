from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal
from uuid import UUID, uuid4

import jwt
from jwt.exceptions import InvalidTokenError as PyJWTError

from app.auth.exceptions import InvalidTokenError
from app.core.config import Settings, get_settings

TokenType = Literal["access", "refresh"]


@dataclass(frozen=True, slots=True)
class TokenClaims:
    subject: UUID
    token_type: TokenType
    issued_at: datetime
    expires_at: datetime
    jti: UUID
    session_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class EncodedToken:
    value: str
    claims: TokenClaims


class JWTService:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def create_access_token(
        self,
        subject: UUID,
        *,
        expires_delta: timedelta | None = None,
        session_id: UUID | None = None,
    ) -> EncodedToken:
        lifetime = expires_delta or timedelta(
            minutes=self.settings.access_token_expire_minutes
        )
        return self._create_token(subject, "access", lifetime, session_id)

    def create_refresh_token(
        self,
        subject: UUID,
        *,
        expires_delta: timedelta | None = None,
        session_id: UUID | None = None,
    ) -> EncodedToken:
        lifetime = expires_delta or timedelta(
            days=self.settings.refresh_token_expire_days
        )
        return self._create_token(subject, "refresh", lifetime, session_id)

    def decode_token(
        self,
        token: str,
        *,
        expected_type: TokenType,
    ) -> TokenClaims:
        try:
            payload = jwt.decode(
                token,
                self.settings.jwt_secret_key.get_secret_value(),
                algorithms=[self.settings.jwt_algorithm],
                issuer=self.settings.jwt_issuer,
                audience=self.settings.jwt_audience,
                options={
                    "require": [
                        "sub",
                        "type",
                        "iat",
                        "exp",
                        "jti",
                        "iss",
                        "aud",
                    ]
                },
            )
            token_type = payload["type"]
            if token_type != expected_type:
                raise InvalidTokenError("Unexpected token type")
            if token_type not in ("access", "refresh"):
                raise InvalidTokenError("Invalid token type")

            subject = UUID(payload["sub"])
            jti = UUID(payload["jti"])
            session_id = (
                UUID(payload["sid"]) if payload.get("sid") is not None else None
            )
            if token_type == "refresh" and session_id is None:
                raise InvalidTokenError("Refresh token is missing a session")
            issued_at = self._timestamp(payload["iat"])
            expires_at = self._timestamp(payload["exp"])
        except (
            PyJWTError,
            KeyError,
            TypeError,
            ValueError,
        ) as error:
            raise InvalidTokenError("Invalid token") from error

        return TokenClaims(
            subject=subject,
            token_type=token_type,
            issued_at=issued_at,
            expires_at=expires_at,
            jti=jti,
            session_id=session_id,
        )

    def _create_token(
        self,
        subject: UUID,
        token_type: TokenType,
        lifetime: timedelta,
        session_id: UUID | None,
    ) -> EncodedToken:
        issued_at = datetime.now(timezone.utc)
        expires_at = issued_at + lifetime
        claims = TokenClaims(
            subject=subject,
            token_type=token_type,
            issued_at=issued_at,
            expires_at=expires_at,
            jti=uuid4(),
            session_id=session_id,
        )
        payload = {
            "sub": str(claims.subject),
            "type": claims.token_type,
            "iat": claims.issued_at,
            "exp": claims.expires_at,
            "jti": str(claims.jti),
            "iss": self.settings.jwt_issuer,
            "aud": self.settings.jwt_audience,
        }
        if claims.session_id is not None:
            payload["sid"] = str(claims.session_id)
        value = jwt.encode(
            payload,
            self.settings.jwt_secret_key.get_secret_value(),
            algorithm=self.settings.jwt_algorithm,
        )
        return EncodedToken(value=value, claims=claims)

    @staticmethod
    def _timestamp(value: object) -> datetime:
        if not isinstance(value, int):
            raise TypeError("Token timestamp must be an integer")
        return datetime.fromtimestamp(value, tz=timezone.utc)
