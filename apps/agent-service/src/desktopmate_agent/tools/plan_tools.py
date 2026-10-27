"""Non-persistent planning tools used by the unified Agent conversation."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from desktopmate_agent.tools.base import BaseTool, RiskLevel, ToolResult


class PlanStep(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    detail: str = Field(default="", max_length=600)
    deliverable: str = Field(default="", max_length=240)
    status: Literal["pending", "in_progress", "done"] = "pending"


class PlanDraftArgs(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    objective: str = Field(min_length=1, max_length=800)
    steps: list[PlanStep] = Field(min_length=2, max_length=12)
    constraints: list[str] = Field(default_factory=list, max_length=8)
    success_criteria: list[str] = Field(default_factory=list, max_length=8)

    @field_validator("constraints", "success_criteria", mode="before")
    @classmethod
    def normalize_string_lists(cls, value: object) -> object:
        """Accept the scalar strings models sometimes emit for array fields."""
        if isinstance(value, str):
            parts = [part.strip(" -*\t") for part in value.replace("\r", "").split("\n")]
            return [part for part in parts if part]
        return value


class PlanDraftTool(BaseTool[PlanDraftArgs]):
    """Build a reviewable plan without creating tasks or changing user data."""

    name = "plan_draft"
    description = "Create a structured, non-persistent execution plan for the current conversation."
    args_schema = PlanDraftArgs
    risk_level = RiskLevel.READ
    requires_permission = False

    async def run(self, args: PlanDraftArgs) -> ToolResult:
        plan = {
            "title": args.title,
            "objective": args.objective,
            "steps": [step.model_dump() for step in args.steps],
            "constraints": args.constraints,
            "success_criteria": args.success_criteria,
        }
        return ToolResult(
            status="success",
            summary=f"已生成计划：{args.title}（尚未执行）",
            data={"plan": plan},
        )
