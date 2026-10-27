"""多代理委派：子代理只读、单任务、并行批次、超时。"""

import asyncio
import time
from pathlib import Path

from desktopmate_agent.config import Settings
from desktopmate_agent.main import create_app
from desktopmate_agent.tools.delegate_tools import DelegateBatchArgs, DelegateBatchTool, DelegateTaskArgs, DelegateTaskTool, SubTask

AUTH_HEADERS = {"X-DesktopMate-Token": "test-token"}


def make_settings(tmp_path: Path) -> Settings:
    return Settings(
        trace_dir=tmp_path / "traces",
        settings_dir=tmp_path / "settings",
        app_data_dir=tmp_path,
        knowledge_dir=tmp_path / "knowledge",
        knowledge_db_path=tmp_path / "desktopmate.db",
        session_db_path=tmp_path / "desktopmate.db",
        auth_token="test-token",
        openai_api_key=None,
        deepseek_api_key=None,
        embedding_provider="fake",
        embedding_dimensions=16,
        allowed_roots=str(tmp_path),
    )


async def fake_runner(goal: str, context: str) -> str:
    await asyncio.sleep(0.01)
    return f"[报告] {goal}"


def test_delegate_task_single(tmp_path):
    tool = DelegateTaskTool(runner=fake_runner)
    result = asyncio.run(tool.run(DelegateTaskArgs(goal="查一下 X", context="背景")))
    assert result.status == "success"
    assert result.data["text"] == "[报告] 查一下 X"


def test_delegate_batch_parallel(tmp_path):
    tool = DelegateBatchTool(runner=fake_runner)

    async def run():
        start = time.monotonic()
        result = await tool.run(
            DelegateBatchArgs(
                subtasks=[
                    SubTask(goal="任务A"),
                    SubTask(goal="任务B"),
                    SubTask(goal="任务C"),
                ]
            )
        )
        return result, time.monotonic() - start

    result, elapsed = asyncio.run(run())
    assert result.status == "success"
    texts = [item["text"] for item in result.data["results"]]
    assert texts == ["[报告] 任务A", "[报告] 任务B", "[报告] 任务C"]
    # 并行：3 个 10ms 任务应远小于 30ms 串行 + 余量
    assert elapsed < 0.2


def test_delegate_batch_max_three(tmp_path):
    from pydantic import ValidationError

    try:
        DelegateBatchArgs(subtasks=[SubTask(goal=f"任务{i}") for i in range(4)])
        assert False, "应拒绝 4 个子任务"
    except ValidationError:
        pass


def test_sub_agent_runner_read_only(tmp_path):
    """经 create_app 装配：委派工具是只读的（子代理不能写入）。"""
    from fastapi.testclient import TestClient

    settings = make_settings(tmp_path)
    with TestClient(create_app(settings=settings)) as client:
        registry = client.app.state.agent_service.tool_registry
        names = {tool.name for tool in registry.list()}
        assert {"delegate_task", "delegate_batch"} <= names
        delegate = registry.get("delegate_batch")
        assert delegate.risk_level.value == "read"


def test_delegate_tools_registered(tmp_path):
    from fastapi.testclient import TestClient

    with TestClient(create_app(settings=make_settings(tmp_path))) as client:
        tools = client.get("/api/tools", headers=AUTH_HEADERS).json()["tools"]
        names = {t["name"] for t in tools}
        assert {"delegate_task", "delegate_batch", "delegate_orchestrate"} <= names


# --- 编排器 ---


def test_parse_decomposition():
    from desktopmate_agent.tools.delegate_tools import parse_decomposition

    raw = '好的，我拆成两个子任务：\n[{"goal": "查A", "context": "背景1"}, {"goal": "查B", "context": ""}]'
    subtasks = parse_decomposition(raw, fallback_request="原请求")
    assert len(subtasks) == 2
    assert subtasks[0].goal == "查A"
    assert subtasks[0].context == "背景1"

    # 超过 3 个只取前 3
    many = parse_decomposition('[{"goal": "1"}, {"goal": "2"}, {"goal": "3"}, {"goal": "4"}]', "f")
    assert len(many) == 3

    # 解析失败 → 退化单子任务
    fallback = parse_decomposition("抱歉，我无法拆解", "整个请求作为子任务")
    assert len(fallback) == 1
    assert fallback[0].goal == "整个请求作为子任务"


def test_delegate_orchestrate_runs_subtasks(tmp_path):
    from desktopmate_agent.tools.delegate_tools import DelegateOrchestrateArgs, DelegateOrchestrateTool

    async def decomposer(request: str):
        return [SubTask(goal=f"子任务{i}") for i in range(3)]

    tool = DelegateOrchestrateTool(decomposer=decomposer, runner=fake_runner)
    result = asyncio.run(tool.run(DelegateOrchestrateArgs(request="并行查三件事")))
    assert result.status == "success"
    assert len(result.data["results"]) == 3
    assert all(item["status"] == "success" for item in result.data["results"])


def test_delegate_orchestrate_decomposer_error(tmp_path):
    from desktopmate_agent.tools.delegate_tools import DelegateOrchestrateArgs, DelegateOrchestrateTool

    async def bad_decomposer(request: str):
        raise RuntimeError("拆解失败")

    tool = DelegateOrchestrateTool(decomposer=bad_decomposer, runner=fake_runner)
    result = asyncio.run(tool.run(DelegateOrchestrateArgs(request="x")))
    assert result.status == "error"


def test_intent_delegate_hints():
    from desktopmate_agent.agent.intent import HeuristicIntentRouter, IntentType

    router = HeuristicIntentRouter()
    for text in [
        "用并行子任务分别查三件事",
        "帮我分头查一下 test.md 和 server.md",
        "同时查知识库里淘宝和拼多多的风格",
    ]:
        decision = router.route(text)
        assert decision.type == IntentType.TOOL_CALL
        assert decision.tool_name == "delegate_orchestrate"
        assert decision.tool_args["request"] == text

    # 普通提问不误入
    normal = router.route("淘宝是什么风格")
    assert not (normal.type == IntentType.TOOL_CALL and normal.tool_name == "delegate_orchestrate")
