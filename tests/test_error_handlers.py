from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.errors import register_exception_handlers
from app.orchestration.exceptions import RoutingError


def test_routing_error_is_a_safe_cors_compatible_service_error() -> None:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/routing-error")
    async def routing_error():
        raise RoutingError("provider details must not be exposed")

    response = TestClient(app).get(
        "/routing-error",
        headers={"Origin": "https://arimafinance.xyz"},
    )

    assert response.status_code == 503
    assert response.json() == {
        "detail": "The orchestration provider is temporarily unavailable"
    }
