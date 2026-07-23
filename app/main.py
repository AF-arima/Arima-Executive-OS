from fastapi import FastAPI

from app.api.v1.router import api_router
from app.core.errors import register_exception_handlers

app = FastAPI()
register_exception_handlers(app)
app.include_router(api_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
