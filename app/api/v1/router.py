from fastapi import APIRouter

from app.api.v1.routes import (
    activity,
    admin,
    analytics,
    auth,
    dashboard,
    notifications,
    projects,
    tasks,
)

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth.router)
api_router.include_router(admin.router)
api_router.include_router(projects.router)
api_router.include_router(tasks.router)
api_router.include_router(dashboard.router)
api_router.include_router(analytics.router)
api_router.include_router(activity.router)
api_router.include_router(notifications.router)
