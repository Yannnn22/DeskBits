"""多代理委派工具：子代理只读、可并行、有超时。"""

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import ClassVar

from pydantic import BaseModel, Field

from desktopmate_agent.tools.base import BaseTool, RiskLevel, ToolResult


class SubTask(BaseModel):
    goal: str = Field(min_length=1, max_length=2000)
    context: str = Field(default="", max_length=4000)


class DelegateTaskArgs(BaseModel):
    goal: str = Field(min_length=1, max_length=2000)
    context: str = Field(default="", max_length=4000)


class DelegateBatchArgs(BaseModel):
    subtasks: list[SubTask] = Field(min_length=1, max_length=3)


Runner = Callable[[str, str], Awaitable[str]]
ProgressCallback = Callable[[dict], Awaitable[None]]

_SUB_TIMEOUT = 180


def parse_decomposition(raw: str, fallback_request: str) -> list[SubTask]:
    """解析模型拆解结果；解析失败时退化为单子任务（整个请求）。"""
    text = raw.strip()
    start, end = text.find("["), text.rfind("]")
    if start >= 0 and end > start:
        text = text[start : end + 1]
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return [SubTask(goal=fallback_request)]
    if not isinstance(data, list):
        return [SubTask(goal=fallback_request)]
    subtasks: list[SubTask] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        goal = str(item.get("goal") or "").strip()
        if not goal:
            continue
        subtasks.append(SubTask(goal=goal[:2000], context=str(item.get("context") or "")[:4000]))
        if len(subtasks) >= 3:
            break
    return subtasks or [SubTask(goal=fallback_request)]


class DelegateTaskTool(BaseTool[DelegateTaskArgs]):
    name = "delegate_task"
    description = (
        "派发一个子任务给只读子代理执行（子代理可搜索文件与知识库，不能写入），"
        "返回子代理的报告。适合拆解独立的小任务。"
    )
    args_schema = DelegateTaskArgs
    risk_level = RiskLevel.READ
    requires_permission = False
    timeout_seconds: ClassVar[float] = 300.0

    def __init__(self, runner: Runner) -> None:
        self.runner = runner

    async def run(self, args: DelegateTaskArgs) -> ToolResult:
        try:
            text = await asyncio.wait_for(self.runner(args.goal, args.context), timeout=_SUB_TIMEOUT)
        except asyncio.TimeoutError:
            return ToolResult(status="error", summary="子任务执行超时", data={"error": "timeout"})
        except Exception as exc:
            return ToolResult(status="error", summary=f"子任务执行失败：{exc}", data={"error": str(exc)})
        return ToolResult(status="success", summary="子任务完成", data={"text": text})


class DelegateBatchTool(BaseTool[DelegateBatchArgs]):
    name = "delegate_batch"
    description = (
        "并行派发最多 3 个只读子任务（子代理可搜索文件与知识库，不能写入），"
        "返回每个子任务的结果列表。适合同时对比多份资料。"
    )
    args_schema = DelegateBatchArgs
    risk_level = RiskLevel.READ
    requires_permission = False
    timeout_seconds: ClassVar[float] = 300.0

    def __init__(
        self,
        runner: Runner,
        progress_callback: ProgressCallback | None = None,
    ) -> None:
        self.runner = runner
        self.progress_callback = progress_callback

    async def run(self, args: DelegateBatchArgs) -> ToolResult:
        async def emit(payload: dict) -> None:
            if self.progress_callback is not None:
                await self.progress_callback(payload)

        async def run_one(subtask: SubTask) -> dict:
            goal = subtask.goal[:120]
            await emit({"goal": goal, "status": "running"})
            try:
                text = await asyncio.wait_for(
                    self.runner(subtask.goal, subtask.context), timeout=_SUB_TIMEOUT
                )
                await emit({"goal": goal, "status": "completed"})
                return {"goal": goal, "status": "success", "text": text}
            except asyncio.TimeoutError:
                await emit({"goal": goal, "status": "failed", "error": "timeout"})
                return {"goal": goal, "status": "error", "text": "(子任务超时)"}
            except Exception as exc:
                await emit({"goal": goal, "status": "failed", "error": str(exc)})
                return {"goal": goal, "status": "error", "text": f"(子任务失败：{exc})"}

        results = await asyncio.gather(*(run_one(sub) for sub in args.subtasks))
        return ToolResult(
            status="success",
            summary=f"{len(results)} 个子任务完成",
            data={"results": results},
        )


class DelegateOrchestrateArgs(BaseModel):
    request: str = Field(min_length=1, max_length=4000)


class DelegateOrchestrateTool(BaseTool[DelegateOrchestrateArgs]):
    """编排器：把自然语言请求拆成 ≤3 个子任务并并行执行。

    拆解由模型完成（decomposer），执行用只读子代理（runner）。
    """

    name = "delegate_orchestrate"
    description = (
        "把用户请求拆成最多 3 个可并行执行的只读子任务（子代理可搜索文件、读文件、"
        "查知识库，不能写入），并行执行后汇总返回。适合「分别查/对比/同时整理」类请求。"
    )
    args_schema = DelegateOrchestrateArgs
    risk_level = RiskLevel.READ
    requires_permission = False
    timeout_seconds: ClassVar[float] = 300.0

    def __init__(
        self,
        *,
        decomposer: Callable[[str], Awaitable[list[SubTask]]],
        runner: Runner,
        progress_callback: ProgressCallback | None = None,
    ) -> None:
        self.decomposer = decomposer
        self.runner = runner
        self.progress_callback = progress_callback

    async def run(self, args: DelegateOrchestrateArgs) -> ToolResult:
        try:
            subtasks = await asyncio.wait_for(self.decomposer(args.request), timeout=60)
        except Exception as exc:
            return ToolResult(status="error", summary=f"任务拆解失败：{exc}", data={"error": str(exc)})
        if not subtasks:
            return ToolResult(status="error", summary="任务拆解为空", data={"error": "empty"})

        async def emit(payload: dict) -> None:
            if self.progress_callback is not None:
                await self.progress_callback(payload)

        async def run_one(subtask: SubTask) -> dict:
            goal = subtask.goal[:120]
            await emit({"goal": goal, "status": "running"})
            try:
                text = await asyncio.wait_for(
                    self.runner(subtask.goal, subtask.context), timeout=_SUB_TIMEOUT
                )
                await emit({"goal": goal, "status": "completed"})
                return {"goal": goal, "status": "success", "text": text}
            except asyncio.TimeoutError:
                await emit({"goal": goal, "status": "failed", "error": "timeout"})
                return {"goal": goal, "status": "error", "text": "(子任务超时)"}
            except Exception as exc:
                await emit({"goal": goal, "status": "failed", "error": str(exc)})
                return {"goal": goal, "status": "error", "text": f"(子任务失败：{exc})"}

        results = await asyncio.gather(*(run_one(sub) for sub in subtasks))
        return ToolResult(
            status="success",
            summary=f"{len(results)} 个子任务完成",
            data={"results": results},
        )


class PipelineStage(BaseModel):
    """流水线的一个阶段：指定 worker 与任务描述。

    worker: worker 名称（由编排器注册，如 researcher / writer）。
    task: 该阶段要完成的任务描述（可引用上一阶段输出，用 {previous} 占位）。
    """

    worker: str = Field(min_length=1, max_length=60)
    task: str = Field(min_length=1, max_length=2000)


class DelegatePipelineArgs(BaseModel):
    """流水线编排请求：阶段按顺序执行，前一阶段输出注入后一阶段。"""

    goal: str = Field(min_length=1, max_length=2000)
    stages: list[PipelineStage] = Field(min_length=1, max_length=5)


PipelineRunner = Callable[[str, str], Awaitable[str]]


class DelegatePipelineTool(BaseTool[DelegatePipelineArgs]):
    """流水线编排器：多阶段按依赖顺序执行（A 的结果喂给 B）。

    与 delegate_orchestrate（并行）互补：适合「先调研后写作」「先分析后出方案」
    这类有前后依赖的任务。每个阶段由指定 worker 执行，输出注入下一阶段上下文。
    """

    name = "delegate_pipeline"
    description = (
        "把任务拆成最多 5 个按顺序执行的阶段（如先研究再写作），"
        "每个阶段由对应 worker 完成，前一阶段的输出自动作为后一阶段的背景上下文。"
        "适合有先后依赖的复杂任务。"
    )
    args_schema = DelegatePipelineArgs
    risk_level = RiskLevel.READ
    requires_permission = False
    timeout_seconds: ClassVar[float] = 600.0

    def __init__(
        self,
        *,
        workers: dict[str, PipelineRunner],
        progress_callback: ProgressCallback | None = None,
    ) -> None:
        self.workers = workers
        self.progress_callback = progress_callback

    async def run(self, args: DelegatePipelineArgs) -> ToolResult:
        unknown = [s.worker for s in args.stages if s.worker not in self.workers]
        if unknown:
            return ToolResult(
                status="error",
                summary=f"未知 worker: {', '.join(sorted(set(unknown)))}",
                data={"error": "unknown_worker"},
            )

        async def emit(payload: dict) -> None:
            if self.progress_callback is not None:
                await self.progress_callback(payload)

        previous = ""
        stage_results: list[dict[str, str]] = []
        for index, stage in enumerate(args.stages):
            await emit({"stage": stage.worker, "status": "running", "index": index})
            task = stage.task.replace("{previous}", previous[:8000])
            try:
                text = await asyncio.wait_for(
                    self.workers[stage.worker](task, previous),
                    timeout=_SUB_TIMEOUT,
                )
                await emit({"stage": stage.worker, "status": "completed", "index": index})
                previous = text
                stage_results.append({"stage": stage.worker, "text": text})
            except asyncio.TimeoutError:
                await emit({"stage": stage.worker, "status": "failed", "error": "timeout", "index": index})
                stage_results.append({"stage": stage.worker, "text": "(该阶段超时)", "error": "timeout"})
                break
            except Exception as exc:
                await emit({"stage": stage.worker, "status": "failed", "error": str(exc), "index": index})
                stage_results.append({"stage": stage.worker, "text": f"(该阶段失败：{exc})", "error": str(exc)})
                break

        return ToolResult(
            status="success",
            summary=f"{len(stage_results)}/{len(args.stages)} 个阶段完成",
            data={"results": stage_results},
        )
