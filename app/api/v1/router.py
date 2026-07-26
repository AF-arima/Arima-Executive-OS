from fastapi import APIRouter

from app.api.v1.routes import (
    activity,
    admin,
    agents,
    analytics,
    auth,
    crm,
    dashboard,
    notifications,
    outreach,
    projects,
    tasks,
)

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth.router)
api_router.include_router(admin.router)
api_router.include_router(agents.router)
api_router.include_router(projects.router)
api_router.include_router(tasks.router)
api_router.include_router(dashboard.router)
api_router.include_router(analytics.router)
api_router.include_router(activity.router)
api_router.include_router(notifications.router)
api_router.include_router(crm.router)
api_router.include_router(outreach.router)
