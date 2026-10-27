from abc import ABC, abstractmethod
from enum import StrEnum
from typing import Any, ClassVar, Generic, TypeVar

from pydantic import BaseModel, Field


class RiskLevel(StrEnum):
    READ = "read"
    WRITE = "write"
    SYSTEM = "system"
    EXTERNAL = "external"


class ToolResult(BaseModel):
    status: str
    summary: str
    data: dict[str, Any] = Field(default_factory=dict)


ArgsT = TypeVar("ArgsT", bound=BaseModel)


class BaseTool(ABC, Generic[ArgsT]):
    name: ClassVar[str]
    description: ClassVar[str]
    args_schema: ClassVar[type[ArgsT]]
    risk_level: ClassVar[RiskLevel]
    requires_permission: ClassVar[bool]
    timeout_seconds: ClassVar[float] = 30.0
    # Internal persistence helpers remain callable by REST/UI flows but are not
    # offered to the model as first-class actions.
    agent_visible: ClassVar[bool] = True

    def parse_args(self, raw_args: dict[str, Any]) -> ArgsT:
        return self.args_schema.model_validate(raw_args)

    async def approval_preview(self, raw_args: dict[str, Any]) -> dict[str, Any] | None:
        """Return a user-reviewable snapshot before a protected action."""
        return None

    @abstractmethod
    async def run(self, args: ArgsT) -> ToolResult:
        raise NotImplementedError
