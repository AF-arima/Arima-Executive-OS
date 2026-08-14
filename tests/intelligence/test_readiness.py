from fastapi.testclient import TestClient
import pytest

from app.main import app


class ReadyConnection:
    async def __aenter__(self) -> "ReadyConnection":
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def execute(self, _: object) -> None:
        return None


class ReadyEngine:
    def connect(self) -> ReadyConnection:
        return ReadyConnection()


def test_canonical_readiness_route_and_browser_secret_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.main.engine", ReadyEngine())

    response = TestClient(app).get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready", "database": "ok"}
    assert "/api/v1/ready" not in app.openapi()["paths"]
    cors = next(
        middleware
        for middleware in app.user_middleware
        if middleware.cls.__name__ == "CORSMiddleware"
    )
    assert "X-Telegram-Bot-Api-Secret-Token" not in cors.kwargs["allow_headers"]
