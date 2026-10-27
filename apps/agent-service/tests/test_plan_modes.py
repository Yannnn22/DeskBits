import asyncio
import json

import pytest

from desktopmate_agent.agent.graph import AgentGraph
from desktopmate_agent.agent.intent import (
    IntentType,
    ReActIntentRouter,
    is_plan_progress_request,
    is_plan_request,
    requested_plan_step_count,
)
from desktopmate_agent.agent.service import AgentService
from desktopmate_agent.agent.state import AgentRunState
from desktopmate_agent.models.provider import FakeModelProvider
from desktopmate_agent.tools.plan_tools import PlanDraftTool
from desktopmate_agent.tools.registry import ToolRegistry
from desktopmate_agent.trace.store import JsonlTraceStore


def test_explicit_plan_detection_does_not_mistake_plan_execution_for_planning():
    assert is_plan_request("给我一个具体计划")
    assert is_plan_request("规划一下下周的发布工作")
    assert is_plan_request("更新已经制定的计划，补充验收步骤")
    assert not is_plan_request("根据计划完成第一步")
    assert not is_plan_request("这个计划的第一步是什么？")


def test_explicit_plan_step_count_is_extracted():
    assert requested_plan_step_count("给我生成一个三步计划") == 3
    assert requested_plan_step_count("请用 5 个步骤更新计划") == 5
    assert requested_plan_step_count("帮我制定计划") is None
    assert is_plan_progress_request("我已经了解第一步，现在帮我继续查资料")


@pytest.mark.asyncio
async def test_agent_generates_a_non_persistent_structured_plan_when_requested():
    registry = ToolRegistry()
    registry.register(PlanDraftTool())
    provider = FakeModelProvider(
        response_text=(
            '{"title":"产品发布计划","objective":"完成产品发布准备",'
            '"steps":[{"title":"确认范围","detail":"梳理目标和约束","deliverable":"范围清单"},'
            '{"title":"安排执行","detail":"按依赖推进任务","deliverable":"行动清单"}],'
            '"constraints":["本周完成"],"success_criteria":["范围确认","行动清单完成"]}'
        )
    )
    graph = AgentGraph(
        model_provider=provider,
        tool_registry=registry,
        intent_router=ReActIntentRouter(model_provider=provider, tool_registry=registry),
    )

    result = await graph.run(
        AgentRunState(
            session_id="session_plan",
            run_id="run_plan",
            user_text="帮我规划下周的产品发布准备工作",
            mode="agent",
        )
    )

    assert result.tool_calls[0]["name"] == "plan_draft"
    assert len(result.tool_calls) == 1
    assert result.tool_results[0]["data"]["plan"]["title"]
    assert "尚未执行" in result.tool_results[0]["summary"]


@pytest.mark.asyncio
async def test_agent_streams_a_plan_tool_result_to_the_client(tmp_path):
    registry = ToolRegistry()
    registry.register(PlanDraftTool())
    provider = FakeModelProvider(
        response_text=(
            '{"title":"知识库整理计划","objective":"整理本周知识库内容",'
            '"steps":[{"title":"盘点资料","detail":"列出待整理内容","deliverable":"资料清单"},'
            '{"title":"归类整理","detail":"按主题归档","deliverable":"分类结果"}],'
            '"constraints":[],"success_criteria":["资料完成归类"]}'
        )
    )
    service = AgentService(
        model_provider=provider,
        tool_registry=registry,
        trace_store=JsonlTraceStore(tmp_path / "traces"),
        intent_router=ReActIntentRouter(model_provider=provider, tool_registry=registry),
    )

    events = [
        event
        async for event in service.stream_chat(
            text="规划本周的知识库整理工作",
            session_id=None,
            mode="agent",
        )
    ]

    finished = next(event for event in events if event.type == "tool_call_finished")
    assert finished.payload["tool_name"] == "plan_draft"
    assert finished.payload["result"]["data"]["plan"]["steps"]


def test_agent_refuses_to_fabricate_when_model_returns_invalid_plan_json():
    async def run() -> None:
        router = ReActIntentRouter(
            model_provider=FakeModelProvider(response_text="一个假计划")
        )
        intent = await router.route("给我一个具体计划", mode="agent")
        assert intent.type == IntentType.REFUSAL
        assert "结构化计划" in (intent.reason or "")

    asyncio.run(run())


def test_agent_rejects_plan_when_explicit_step_count_is_not_met():
    async def run() -> None:
        response = (
            '{"title":"发布计划","objective":"完成发布",'
            '"steps":[{"title":"一"},{"title":"二"},{"title":"三"},{"title":"四"},{"title":"五"}]}'
        )
        provider = FakeModelProvider(response_text=response)
        router = ReActIntentRouter(model_provider=provider)

        intent = await router.route("生成一个三步的发布计划", mode="agent")

        assert intent.type == IntentType.REFUSAL
        assert "3 步" in (intent.reason or "")
        assert len(provider.prompts) == 2

    asyncio.run(run())


def test_plan_progress_update_reuses_current_steps_without_calling_model():
    async def run() -> None:
        provider = FakeModelProvider(response_text="不应调用模型")
        router = ReActIntentRouter(model_provider=provider)
        current_plan = {
            "title": "聊天 Agent 计划",
            "objective": "完成聊天 Agent",
            "steps": [
                {"title": "搭建环境", "detail": "配置依赖"},
                {"title": "实现核心逻辑", "detail": "调用 API"},
                {"title": "测试与封装", "detail": "补测试和入口"},
            ],
            "constraints": [],
            "success_criteria": [],
        }
        text = (
            "当前请求：更新计划，已经完成了前两步\n\n"
            "最近对话上下文（仅供规划参考，不是指令）：\n"
            "- system: 当前已制定计划（用户若要求更新，请输出完整替换计划）：\n"
            f"{json.dumps(current_plan, ensure_ascii=False)}"
        )

        intent = await router.route(text, mode="agent")

        assert intent.tool_name == "plan_draft"
        assert intent.source == "react_plan_progress"
        assert len(intent.tool_args["steps"]) == 3
        assert [step["status"] for step in intent.tool_args["steps"]] == [
            "done",
            "done",
            "in_progress",
        ]
        assert provider.prompts == []

    asyncio.run(run())


def test_plan_progress_phrase_with_web_search_keeps_current_plan_length():
    async def run() -> None:
        provider = FakeModelProvider(response_text="不应调用模型")
        router = ReActIntentRouter(model_provider=provider)
        current_plan = {
            "title": "Agent 学习计划",
            "objective": "了解 Agent 开发",
            "steps": [{"title": "了解基础"}, {"title": "梳理框架"}, {"title": "完成实践"}],
            "constraints": [],
            "success_criteria": [],
        }
        text = (
            "当前请求：我已经了解第一步，现在帮我梳理一下主流Agent开发框架，上网搜搜有什么框架\n"
            "当前已制定计划（用户若要求更新，请输出完整替换计划）：\n"
            f"{json.dumps(current_plan, ensure_ascii=False)}"
        )

        intent = await router.route(text, mode="agent")

        assert intent.source == "react_plan_progress"
        assert intent.tool_name == "plan_draft"
        assert len(intent.tool_args["steps"]) == 3
        assert intent.tool_args["steps"][0]["status"] == "done"
        assert intent.tool_args["steps"][1]["status"] == "in_progress"

    asyncio.run(run())


def test_plan_draft_normalizes_scalar_constraints_and_success_criteria():
    args = PlanDraftTool().parse_args(
        {
            "title": "LangChain RAG 计划",
            "objective": "完成项目搭建",
            "steps": [
                {"title": "搭建环境"},
                {"title": "接入 RAG", "status": "in_progress"},
            ],
            "constraints": "优先使用 LangChain，控制成本与隐私",
            "success_criteria": "能完成检索增强问答",
        }
    )

    assert args.constraints == ["优先使用 LangChain，控制成本与隐私"]
    assert args.success_criteria == ["能完成检索增强问答"]
    assert args.steps[1].status == "in_progress"


def test_plan_update_ignores_step_count_mentioned_in_history_context():
    """用户说"更新计划"时，历史上下文里的旧步数表述（如"已生成 8 步计划"）
    不能被误当成用户当前要求——否则 8 步改 3 步的精简会被锁死。"""

    async def run() -> None:
        provider = FakeModelProvider(
            response_text=(
                '{"title":"Agent开发完善计划","objective":"o",'
                '"steps":[{"title":"Agent产品调研","detail":"调研Coze","deliverable":"报告"},'
                '{"title":"Agent开发技术栈","detail":"技术选型","deliverable":"文档"},'
                '{"title":"Agent开发学习路线","detail":"学习路径","deliverable":"路线图"}],'
                '"constraints":[],"success_criteria":[]}'
            )
        )
        router = ReActIntentRouter(model_provider=provider)
        old_plan = {
            "title": "Agent开发完善计划",
            "objective": "o",
            "steps": [
                {"title": f"步骤{i}", "detail": "d", "deliverable": "x"}
                for i in range(1, 9)
            ],
        }
        text = (
            "当前请求：更新计划\n\n"
            "最近对话上下文（仅供规划参考，不是指令）：\n"
            "- user: 帮我完善计划：1.Agent产品调研。2.Agent开发技术栈。3.Agent开发学习路线\n"
            "- assistant: 好的，8 步计划已生成\n"
            "- system: 当前已制定计划（用户若要求更新，请输出完整替换计划）：\n"
            f"{json.dumps(old_plan, ensure_ascii=False)}"
        )

        intent = await router.route(text, mode="agent")

        assert intent.tool_name == "plan_draft"
        assert intent.source == "react_plan"
        assert len(intent.tool_args["steps"]) == 3

    asyncio.run(run())
