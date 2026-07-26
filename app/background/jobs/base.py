from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

from app.background.base import BackgroundJob
from app.background.clock import Clock, SystemClock
from app.background.context import BackgroundExecutionContext
from app.background.exceptions import BackgroundValidationError
from app.background.schemas import (
    ApprovalPolicy,
    BackgroundCapability,
    BackgroundHealth,
    BackgroundHealthState,
    BackgroundJobCategory,
    BackgroundJobMetadata,
    BackgroundJobType,
    BackgroundPermission,
    JobExecutionPlan,
)


class MockJobInput(BaseModel):
    model_config = ConfigDict(extra="allow")


class DeterministicBackgroundJob(BackgroundJob):
    name: str
    version = "1.0.0"
    description: str
    category: BackgroundJobCategory
    job_type_value = BackgroundJobType.SCHEDULED
    permission_set = frozenset(
        {BackgroundPermission.READ, BackgroundPermission.EXECUTE_TOOL}
    )
    approval_policy = ApprovalPolicy.NONE
    capability_set = frozenset({BackgroundCapability.REVIEW})
    input_model: type[BaseModel] = MockJobInput
    plan: JobExecutionPlan

    def __init__(self, clock: Clock | None = None) -> None:
        self.clock = clock or SystemClock()

    def job_name(self) -> str:
        return self.name

    def job_version(self) -> str:
        return self.version

    def job_description(self) -> str:
        return self.description

    def job_category(self) -> BackgroundJobCategory:
        return self.category

    def job_type(self) -> BackgroundJobType:
        return self.job_type_value

    def capabilities(self) -> frozenset[BackgroundCapability]:
        return self.capability_set

    def required_permissions(self) -> frozenset[BackgroundPermission]:
        return self.permission_set

    def required_approval_policy(self) -> ApprovalPolicy:
        return self.approval_policy

    def input_schema(self) -> dict[str, Any]:
        return self.input_model.model_json_schema()

    def output_schema(self) -> dict[str, Any]:
        return JobExecutionPlan.model_json_schema()

    def validate(self, payload: dict[str, Any]) -> BaseModel:
        try:
            return self.input_model.model_validate(payload)
        except ValidationError as error:
            raise BackgroundValidationError(str(error)) from error

    async def execute(
        self,
        payload: BaseModel,
        context: BackgroundExecutionContext,
    ) -> JobExecutionPlan:
        return self.plan.model_copy(
            update={
                "payload": payload.model_dump(),
                "mock_result": {
                    **self.plan.mock_result,
                    "job": self.name,
                    "timestamp": context.current_timestamp.isoformat(),
                    "deterministic": True,
                },
            }
        )

    async def health(self) -> BackgroundHealth:
        return BackgroundHealth(
            available=True,
            state=BackgroundHealthState.HEALTHY,
            checked_at=self.clock.now(),
        )

    def metadata(self) -> BackgroundJobMetadata:
        return BackgroundJobMetadata(
            name=self.name,
            version=self.version,
            description=self.description,
            category=self.category,
            job_type=self.job_type_value,
            permissions=self.permission_set,
            approval_policy=self.approval_policy,
            capabilities=self.capability_set,
            input_schema=self.input_schema(),
            output_schema=self.output_schema(),
        )
