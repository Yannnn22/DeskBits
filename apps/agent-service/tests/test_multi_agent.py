"""多 Agent Worker 抽象与流水线编排测试。"""

from __future__ import annotations

import asyncio

import pytest
from pydantic import BaseModel, Field

from desktopmate_agent.agents.worker import AgentWorker
from desktopmate_agent.models.provider import FakeModelProvider
from desktopmate_agent.tools.base import BaseTool, RiskLevel, ToolResult
from desktopmate_agent.tools.delegate_tools import DelegatePipelineTool
from desktopmate_agent.tools.registry import ToolRegistry


class ReadArgs(BaseModel):
    path: str = Field(default="", max_length=100)


class reader_tool(BaseTool):
    name = "reader"
    description = "读取文件"
    args_schema = ReadArgs
    risk_level = RiskLevel.READ

    async def run(self, args: ReadArgs) -> ToolResult:
        return ToolResult(status="success", summary="文件内容：hello", data={})


def make_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(reader_tool())
    registry.set_enabled("reader", True)
    return registry


def make_provider() -> FakeModelProvider:
    return FakeModelProvider("ok")


def test_worker_whitelist_filters_tools() -> None:
    registry = make_registry()
    worker = AgentWorker(
        name="researcher",
        system_prompt="你是研究员",
        tool_names=["reader"],
        model_provider=make_provider(),
        tool_registry=registry,
    )
    assert [t.name for t in worker.graph.tool_registry.list()] == ["reader"]


def test_worker_unknown_tool_rejected() -> None:
    registry = make_registry()
    with pytest.raises(ValueError, match="未注册工具"):
        AgentWorker(
            name="bad",
            system_prompt="x",
            tool_names=["nonexistent"],
            model_provider=make_provider(),
            tool_registry=registry,
        )


def test_worker_disabled_tool_passthrough() -> None:
    registry = make_registry()
    registry.set_enabled("reader", False)
    worker = AgentWorker(
        name="r",
        system_prompt="x",
        tool_names=["reader"],
        model_provider=make_provider(),
        tool_registry=registry,
    )
    assert not worker.graph.tool_registry.is_enabled("reader")


def test_worker_json_output_format() -> None:
    registry = make_registry()
    worker = AgentWorker(
        name="j",
        system_prompt="你是助手",
        tool_names=["reader"],
        model_provider=make_provider(),
        tool_registry=registry,
        output_format="json",
    )
    assert "合法 JSON 对象" in worker.graph._SYSTEM_PROMPT


def test_worker_invalid_output_format_rejected() -> None:
    registry = make_registry()
    with pytest.raises(ValueError, match="output_format"):
        AgentWorker(
            name="bad",
            system_prompt="x",
            tool_names=["reader"],
            model_provider=make_provider(),
            tool_registry=registry,
            output_format="xml",
        )


def test_worker_run_returns_text() -> None:
    registry = make_registry()
    worker = AgentWorker(
        name="researcher",
        system_prompt="你是研究员",
        tool_names=["reader"],
        model_provider=make_provider(),
        tool_registry=registry,
    )
    text = asyncio.run(worker.run("查一下文件", "背景是测试"))
    assert isinstance(text, str)
    assert text


def test_worker_as_runner_signature() -> None:
    registry = make_registry()
    worker = AgentWorker(
        name="researcher",
        system_prompt="你是研究员",
        tool_names=["reader"],
        model_provider=make_provider(),
        tool_registry=registry,
    )
    runner = worker.as_runner()
    assert callable(runner)
    text = asyncio.run(runner("goal", "context"))  # type: ignore[arg-type]
    assert isinstance(text, str)


def test_pipeline_runs_stages_in_order_with_previous() -> None:
    """流水线：阶段 A 输出作为阶段 B 的上下文（{previous} 占位替换）。"""
    collected: list[dict] = []

    async def fake_researcher(goal: str, context: str) -> str:
        return f"研究结果：{goal}"

    async def fake_writer(task: str, context: str) -> str:
        collected.append({"task": task, "context": context})
        return f"报告：{task}"

    tool = DelegatePipelineTool(
        workers={"researcher": fake_researcher, "writer": fake_writer},
        progress_callback=None,
    )
    result = asyncio.run(
        tool.run(
            tool.args_schema(
                goal="调研 Agent 框架",
                stages=[
                    {"worker": "researcher", "task": "调研主流框架"},
                    {"worker": "writer", "task": "基于 {previous} 写报告"},
                ],
            )
        )
    )
    assert result.status == "success"
    assert len(collected) == 1
    # writer 收到的 task 里 {previous} 已被上一阶段输出替换
    assert "研究结果" in collected[0]["task"]
    assert "研究结果：调研主流框架" in collected[0]["context"]


def test_pipeline_unknown_worker_rejected() -> None:
    tool = DelegatePipelineTool(workers={"researcher": lambda g, c: g})
    result = asyncio.run(
        tool.run(
            tool.args_schema(
                goal="x",
                stages=[{"worker": "ghost", "task": "do something"}],
            )
        )
    )
    assert result.status == "error"
    assert "未知 worker" in result.summary


def test_direct_intent_router_no_recursive_delegation() -> None:
    """worker 直通路由器：任务初始不路由工具（防止子任务误触发编排递归）。"""
    import asyncio

    from desktopmate_agent.agents.worker import DirectIntentRouter
    from desktopmate_agent.agent.intent import IntentType

    registry = make_registry()
    router = DirectIntentRouter(
        model_provider=make_provider(),
        tool_registry=registry,
    )
    decision = asyncio.run(router.route("深度调研一下 X", mode="agent"))
    assert decision.type == IntentType.DIRECT_RESPONSE
    assert decision.tool_name is None


def test_direct_intent_router_blocks_out_of_whitelist_tool() -> None:
    """worker 路由：observation 阶段模型若返回白名单外工具 → 降级直接回答。"""
    import asyncio

    from desktopmate_agent.agents.worker import DirectIntentRouter
    from desktopmate_agent.agent.intent import IntentType
    from desktopmate_agent.models.provider import FakeModelProvider

    registry = make_registry()

    # FakeModelProvider 返回的 JSON 会被解析为意图；构造一个返回 delegate_pipeline 的
    # 假 provider，验证被白名单拦截。
    class DelegatingProvider(FakeModelProvider):
        async def complete(self, prompt: str) -> str:
            return '{"type": "tool_call", "tool_name": "delegate_pipeline", "tool_args": {}, "confidence": 0.9}'

    router = DirectIntentRouter(
        model_provider=DelegatingProvider("ok"),  # type: ignore[arg-type]
        tool_registry=registry,
    )
    decision = asyncio.run(router.route("继续", mode="agent", observation="工具结果"))
    assert decision.type == IntentType.DIRECT_RESPONSE
