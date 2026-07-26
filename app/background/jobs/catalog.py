from app.background.jobs.base import DeterministicBackgroundJob
from app.background.schemas import (
    BackgroundCapability,
    BackgroundJobCategory,
    BackgroundPermission,
    JobExecutionPlan,
)


class ExecutiveBriefingJob(DeterministicBackgroundJob):
    name = "executive_briefing"
    description = "Prepare a deterministic executive briefing."
    category = BackgroundJobCategory.EXECUTIVE
    permission_set = frozenset(
        {BackgroundPermission.READ, BackgroundPermission.EXECUTE_AGENT}
    )
    capability_set = frozenset(
        {BackgroundCapability.REVIEW, BackgroundCapability.AGENT_EXECUTION}
    )
    plan = JobExecutionPlan(target="agent", target_name="mock")


class ProjectStatusReviewJob(DeterministicBackgroundJob):
    name = "project_status_review"
    description = "Review project status analytics."
    category = BackgroundJobCategory.PROJECTS
    plan = JobExecutionPlan(
        target="internal_tool", target_name="project.analytics"
    )


class OverdueTaskReviewJob(DeterministicBackgroundJob):
    name = "overdue_task_review"
    description = "Review overdue task analytics."
    category = BackgroundJobCategory.TASKS
    plan = JobExecutionPlan(
        target="internal_tool", target_name="task.analytics"
    )


class CRMFollowUpReviewJob(DeterministicBackgroundJob):
    name = "crm_follow_up_review"
    description = "Review CRM pipeline follow-up indicators."
    category = BackgroundJobCategory.CRM
    plan = JobExecutionPlan(
        target="internal_tool", target_name="pipeline.analytics"
    )


class UnreadNotificationReviewJob(DeterministicBackgroundJob):
    name = "unread_notification_review"
    description = "Review unread internal notifications."
    category = BackgroundJobCategory.NOTIFICATIONS
    plan = JobExecutionPlan(
        target="internal_tool", target_name="notification.unread"
    )


class PlatformHealthReviewJob(DeterministicBackgroundJob):
    name = "platform_health_review"
    description = "Review internal platform health."
    category = BackgroundJobCategory.HEALTH
    capability_set = frozenset(
        {BackgroundCapability.REVIEW, BackgroundCapability.HEALTH}
    )
    plan = JobExecutionPlan(
        target="internal_tool", target_name="platform.health"
    )


class PortfolioSummaryJob(DeterministicBackgroundJob):
    name = "portfolio_summary"
    description = "Build a portfolio summary."
    category = BackgroundJobCategory.PORTFOLIO
    plan = JobExecutionPlan(
        target="internal_tool", target_name="portfolio.summary"
    )


class QuantResearchSummaryJob(DeterministicBackgroundJob):
    name = "quant_research_summary"
    description = "Produce deterministic quantitative research."
    category = BackgroundJobCategory.RESEARCH
    plan = JobExecutionPlan(
        target="mock", mock_result={"summary": "mock quant research"}
    )


class GrowthContentReviewJob(DeterministicBackgroundJob):
    name = "growth_content_review"
    description = "Produce a deterministic growth content review."
    category = BackgroundJobCategory.GROWTH
    plan = JobExecutionPlan(
        target="mock", mock_result={"summary": "mock growth review"}
    )


class IntegrationHealthReviewJob(DeterministicBackgroundJob):
    name = "integration_health_review"
    description = "Review deterministic connector health."
    category = BackgroundJobCategory.INTEGRATIONS
    permission_set = frozenset(
        {
            BackgroundPermission.READ,
            BackgroundPermission.EXECUTE_INTEGRATION,
        }
    )
    capability_set = frozenset(
        {BackgroundCapability.REVIEW, BackgroundCapability.INTEGRATION}
    )
    plan = JobExecutionPlan(
        target="integration",
        target_name="search",
        operation="web_search",
        payload={"query": "integration health"},
    )


class AgentMemoryMaintenanceJob(DeterministicBackgroundJob):
    name = "agent_memory_maintenance"
    description = "Review agent memory without mutating it."
    category = BackgroundJobCategory.MEMORY
    capability_set = frozenset(
        {BackgroundCapability.REVIEW, BackgroundCapability.MAINTENANCE}
    )
    plan = JobExecutionPlan(
        target="internal_tool", target_name="memory.summary"
    )


class SystemAuditReviewJob(DeterministicBackgroundJob):
    name = "system_audit_review"
    description = "Review recent system audit activity."
    category = BackgroundJobCategory.AUDIT
    plan = JobExecutionPlan(
        target="internal_tool", target_name="activity.summary"
    )


BACKGROUND_JOB_TYPES: tuple[
    type[DeterministicBackgroundJob], ...
] = (
    ExecutiveBriefingJob,
    ProjectStatusReviewJob,
    OverdueTaskReviewJob,
    CRMFollowUpReviewJob,
    UnreadNotificationReviewJob,
    PlatformHealthReviewJob,
    PortfolioSummaryJob,
    QuantResearchSummaryJob,
    GrowthContentReviewJob,
    IntegrationHealthReviewJob,
    AgentMemoryMaintenanceJob,
    SystemAuditReviewJob,
)
