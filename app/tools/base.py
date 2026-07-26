from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

from app.tools.context import ToolExecutionContext
from app.tools.exceptions import ToolValidationError
from app.tools.schemas import (
    ToolCapability,
    ToolCategory,
    ToolHealth,
    ToolHealthStatus,
    ToolMetadata,
    ToolPermission,
)


class EmptyToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ToolAdapter(ABC):
    @abstractmethod
    def tool_name(self) -> str: ...

    @abstractmethod
    def tool_description(self) -> str: ...

    @abstractmethod
    def tool_category(self) -> ToolCategory: ...

    @abstractmethod
    def tool_version(self) -> str: ...

    @abstractmethod
    def required_permissions(self) -> frozenset[ToolPermission]: ...

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
        context: ToolExecutionContext,
    ) -> Any: ...

    @abstractmethod
    async def health(self) -> ToolHealth: ...

    @abstractmethod
    def metadata(self) -> ToolMetadata: ...

    @abstractmethod
    def capabilities(self) -> frozenset[ToolCapability]: ...


class InternalToolAdapter(ToolAdapter, ABC):
    name: str
    description: str
    category: ToolCategory
    version = "1.0.0"
    permissions = frozenset({ToolPermission.READ})
    tool_capabilities = frozenset({ToolCapability.READ})
    input_model: type[BaseModel] = EmptyToolInput

    def tool_name(self) -> str:
        return self.name

    def tool_description(self) -> str:
        return self.description

    def tool_category(self) -> ToolCategory:
        return self.category

    def tool_version(self) -> str:
        return self.version

    def required_permissions(self) -> frozenset[ToolPermission]:
        return self.permissions

    def capabilities(self) -> frozenset[ToolCapability]:
        return self.tool_capabilities

    def input_schema(self) -> dict[str, Any]:
        return self.input_model.model_json_schema()

    def output_schema(self) -> dict[str, Any]:
        return {"type": "object"}

    def validate(self, payload: dict[str, Any]) -> BaseModel:
        try:
            return self.input_model.model_validate(payload)
        except ValidationError as error:
            raise ToolValidationError(str(error)) from error

    async def health(self) -> ToolHealth:
        return ToolHealth(
            status=ToolHealthStatus.HEALTHY,
            available=True,
            checked_at=datetime.now(timezone.utc),
        )

    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name=self.tool_name(),
            description=self.tool_description(),
            category=self.tool_category(),
            version=self.tool_version(),
            permissions=self.required_permissions(),
            capabilities=self.capabilities(),
            input_schema=self.input_schema(),
            output_schema=self.output_schema(),
        )
