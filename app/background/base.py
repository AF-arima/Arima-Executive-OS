from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel

from app.background.context import BackgroundExecutionContext
from app.background.schemas import (
    ApprovalPolicy,
    BackgroundCapability,
    BackgroundHealth,
    BackgroundJobCategory,
    BackgroundJobMetadata,
    BackgroundJobType,
    BackgroundPermission,
    JobExecutionPlan,
)


class BackgroundJob(ABC):
    @abstractmethod
    def job_name(self) -> str: ...

    @abstractmethod
    def job_version(self) -> str: ...

    @abstractmethod
    def job_description(self) -> str: ...

    @abstractmethod
    def job_category(self) -> BackgroundJobCategory: ...

    @abstractmethod
    def job_type(self) -> BackgroundJobType: ...

    @abstractmethod
    def capabilities(self) -> frozenset[BackgroundCapability]: ...

    @abstractmethod
    def required_permissions(self) -> frozenset[BackgroundPermission]: ...

    @abstractmethod
    def required_approval_policy(self) -> ApprovalPolicy: ...

    @abstractmethod
    def input_schema(self) -> dict[str, Any]: ...

    @abstractmethod
    def output_schema(self) -> dict[str, Any]: ...

    @abstractmethod
    def validate(self, payload: dict[str, Any]) -> BaseModel: ...

    @abstractmethod
    async def execute(
        self,
        payload: BaseModel,
        context: BackgroundExecutionContext,
    ) -> JobExecutionPlan: ...

    @abstractmethod
    async def health(self) -> BackgroundHealth: ...

    @abstractmethod
    def metadata(self) -> BackgroundJobMetadata: ...
