"""Tools that create candidate workspace artifacts without writing user files."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from pydantic import BaseModel, Field

from desktopmate_agent.artifacts.store import ArtifactStore
from desktopmate_agent.tools.base import BaseTool, RiskLevel, ToolResult


Composer = Callable[[str], Awaitable[str]]


class ArtifactDraftArgs(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    instruction: str = Field(min_length=1, max_length=12_000)
    source_session_id: str | None = Field(default=None, max_length=128)


class ArtifactRevisionArgs(BaseModel):
    artifact_id: str = Field(min_length=1, max_length=128)
    instruction: str = Field(min_length=1, max_length=12_000)


class ArtifactDraftTool(BaseTool[ArtifactDraftArgs]):
    name = "artifact_draft"
    description = "Generate a candidate document artifact. It is not saved to the workspace until the user confirms."
    args_schema = ArtifactDraftArgs
    risk_level = RiskLevel.READ
    requires_permission = False

    def __init__(self, *, store: ArtifactStore, compose: Composer) -> None:
        self.store = store
        self.compose = compose

    async def run(self, args: ArtifactDraftArgs) -> ToolResult:
        content = await self.compose(_draft_prompt(args.title, args.instruction))
        record = self.store.create_draft(
            kind="document",
            title=args.title,
            content=content.strip() or args.instruction,
            source_session_id=args.source_session_id,
        )
        return ToolResult(
            status="success",
            summary=f"已生成候选文档：{record.title}，确认后会保存到工作区。",
            data={"artifact": record.to_dict()},
        )


class ArtifactRevisionTool(BaseTool[ArtifactRevisionArgs]):
    name = "artifact_revise"
    description = "Generate a candidate revision of a workspace artifact. It never overwrites the confirmed version until the user confirms."
    args_schema = ArtifactRevisionArgs
    risk_level = RiskLevel.READ
    requires_permission = False

    def __init__(self, *, store: ArtifactStore, compose: Composer) -> None:
        self.store = store
        self.compose = compose

    async def run(self, args: ArtifactRevisionArgs) -> ToolResult:
        record = self.store.get(args.artifact_id)
        if record is None or record.state == "archived":
            return ToolResult(status="error", summary="要修改的工作区成品不存在", data={"error": "artifact_not_found"})
        base = record.current or record.pending
        if base is None:
            return ToolResult(status="error", summary="该成品没有可修改的内容", data={"error": "artifact_empty"})
        content = await self.compose(_revision_prompt(record.title, base.content, args.instruction))
        revised = self.store.revise(
            record.id,
            content=content.strip() or base.content,
            metadata={"instruction": args.instruction},
        )
        assert revised is not None
        return ToolResult(
            status="success",
            summary=f"已生成「{revised.title}」的候选版本，确认后会替换工作区当前版本。",
            data={"artifact": revised.to_dict(include_pending=True)},
        )


def _draft_prompt(title: str, instruction: str) -> str:
    return (
        "你是 DeskBits 的写作助手。根据用户要求生成一份可直接保存的中文文档草稿。"
        "只输出文档正文，不要解释生成过程，不要使用 Markdown 代码围栏。"
        f"\n\n文档标题：{title}\n用户要求：{instruction}"
    )


def _revision_prompt(title: str, content: str, instruction: str) -> str:
    return (
        "你是 DeskBits 的文档编辑助手。请根据修改要求重写下方文档，保留未要求改变的有效内容。"
        "只输出修改后的完整文档正文，不要说明修改过程，不要使用 Markdown 代码围栏。"
        f"\n\n文档标题：{title}\n当前正文：\n{content}\n\n修改要求：{instruction}"
    )
