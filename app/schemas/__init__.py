"""Application schemas."""

from app.schemas.activity import ActivityItem, ActivityList
from app.schemas.analytics import (
    AnalyticsInterval,
    DashboardSummary,
    ProjectAnalyticsItem,
    ProjectAnalyticsList,
    ProjectAnalyticsSortField,
    TaskAnalyticsResponse,
    TimeSeriesPoint,
    WorkloadAnalyticsItem,
    WorkloadAnalyticsList,
    WorkloadSortField,
)
from app.schemas.auth import (
    CurrentUserResponse,
    RefreshTokenRequest,
    RoleAssignmentRequest,
    RoleResponse,
    TokenResponse,
    UserLogin,
    UserPublicResponse,
    UserRegistration,
)
from app.schemas.common import SortDirection
from app.schemas.project import (
    ProjectCreate,
    ProjectListResponse,
    ProjectResponse,
    ProjectSortField,
    ProjectUpdate,
)
from app.schemas.task import (
    TaskCreate,
    TaskListResponse,
    TaskResponse,
    TaskSortField,
    TaskUpdate,
)
from app.schemas.notification import (
    NotificationList,
    NotificationResponse,
    ReadAllResponse,
    UnreadCountResponse,
)

__all__ = [
    "CurrentUserResponse",
    "ActivityItem",
    "ActivityList",
    "AnalyticsInterval",
    "DashboardSummary",
    "NotificationList",
    "NotificationResponse",
    "ProjectAnalyticsItem",
    "ProjectAnalyticsList",
    "ProjectAnalyticsSortField",
    "ProjectCreate",
    "ProjectListResponse",
    "ProjectResponse",
    "ProjectSortField",
    "ProjectUpdate",
    "RefreshTokenRequest",
    "RoleAssignmentRequest",
    "RoleResponse",
    "SortDirection",
    "TaskCreate",
    "TaskAnalyticsResponse",
    "TaskListResponse",
    "TaskResponse",
    "TaskSortField",
    "TaskUpdate",
    "TimeSeriesPoint",
    "TokenResponse",
    "UserLogin",
    "UserPublicResponse",
    "UserRegistration",
    "ReadAllResponse",
    "UnreadCountResponse",
    "WorkloadAnalyticsItem",
    "WorkloadAnalyticsList",
    "WorkloadSortField",
]
