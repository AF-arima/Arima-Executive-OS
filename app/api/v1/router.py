from fastapi import APIRouter

from app.api.v1.routes import admin, auth, projects, tasks

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth.router)
api_router.include_router(admin.router)
api_router.include_router(projects.router)
api_router.include_router(tasks.router)
