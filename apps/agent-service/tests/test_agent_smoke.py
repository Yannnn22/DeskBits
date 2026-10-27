import asyncio
from datetime import UTC, datetime

import pytest
from pydantic import BaseModel

from desktopmate_agent.agent.service import AgentService
from desktopmate_agent.agent.graph import AgentGraph
from desktopmate_agent.agent.intent import ModelIntentRouter
from desktopmate_agent.agent.state import AgentRunState
from desktopmate_agent.memory.models import MemoryEntry, MemoryScope
from desktopmate_agent.memory.store import JsonlMemoryStore
from desktopmate_agent.models.provider import FakeModelProvider
from desktopmate_agent.runtime.sessions import new_run_id, new_session_id
from desktopmate_agent.sessions.store import SessionStore
from desktopmate_agent.tools.base import BaseTool, RiskLevel, ToolResult
from desktopmate_agent.tools.file_tools import ReadFileTool, SearchFilesTool
from desktopmate_agent.tools.registry import ToolRegistry
from desktopmate_agent.trace.store import JsonlTraceStore


class FailingArgs(BaseModel):
    text: str


class FailingTool(BaseTool[FailingArgs]):
    name = "failing_tool"
    description = "Always fails for tests"
    args_schema = FailingArgs
    risk_level = RiskLevel.READ
    requires_permission = False

    async def run(self, args: FailingArgs) -> ToolResult:
        raise RuntimeError(f"boom: {args.text}")


class ProtectedTool(BaseTool[FailingArgs]):
    name = "protected_tool"
    description = "Requires permission for tests"
    args_schema = FailingArgs
    risk_level = RiskLevel.WRITE
    requires_permission = True

    async def run(self, args: FailingArgs) -> ToolResult:
        return ToolResult(status="success", summary=f"已执行：{args.text}", data={"text": args.text})


def test_session_ids_have_expected_prefixes():
    assert new_session_id().startswith("session_")
    assert new_run_id().startswith("run_")


@pytest.mark.asyncio
async def test_fake_model_provider_returns_response():
    provider = FakeModelProvider(response_text="你好，我是 DesktopMate。")

    text = await provider.complete("hello")

    assert text == "你好，我是 DesktopMate。"


@pytest.mark.asyncio
async def test_agent_graph_records_user_and_assistant_messages():
    graph = AgentGraph(model_provider=FakeModelProvider(response_text="我会帮你处理。"))
    state = AgentRunState(
        session_id="session_test",
        run_id="run_test",
        user_text="帮我整理桌面文件",
    )

    result = await graph.run(state)

    assert result.messages == [
        {"role": "user", "content": "帮我整理桌面文件"},
        {"role": "assistant", "content": "我会帮你处理。"},
    ]


@pytest.mark.asyncio
@pytest.mark.asyncio
async def test_model_router_can_take_a_bounded_follow_up_action(tmp_allowed_root):
    target = tmp_allowed_root / "note.md"
    target.write_text("important context", encoding="utf-8")
    registry = ToolRegistry()
    registry.register(SearchFilesTool(allowed_roots=[tmp_allowed_root]))
    registry.register(ReadFileTool(allowed_roots=[tmp_allowed_root]))

    # The provider needs the concrete path in its second Action. This custom
    # router represents a model's observation-aware planning result.
    class FollowUpRouter:
        def __init__(self) -> None:
            self.calls = 0

        async def route(self, text: str, *, mode: str = "chat", observation: str = ""):
            self.calls += 1
            if observation:
                return ModelIntentRouter(
                    model_provider=FakeModelProvider(
                        response_text=(
                            '{"type":"tool_call","tool_name":"read_file",'
                            f'"tool_args":{{"path":"{target}"}},"confidence":0.9}}'
                        )
                    )
                ).route(text)
            return ModelIntentRouter(
                model_provider=FakeModelProvider(
                    response_text='{"type":"tool_call","tool_name":"search_files","tool_args":{"query":"note","limit":1},"confidence":0.9}'
                )
            ).route(text)

    result = await AgentGraph(
        model_provider=FakeModelProvider(),
        tool_registry=registry,
        intent_router=FollowUpRouter(),
    ).run(AgentRunState(session_id="session_test", run_id="run_test", user_text="找并读取 note", mode="agent"))

    assert [call["name"] for call in result.tool_calls] == ["search_files", "read_file"]
    assert result.tool_step_count == 2


@pytest.mark.asyncio
async def test_agent_graph_can_call_registered_search_tool(tmp_allowed_root):
    note = tmp_allowed_root / "note.md"
    note.write_text("# DesktopMate\n", encoding="utf-8")
    registry = ToolRegistry()
    registry.register(SearchFilesTool(allowed_roots=[tmp_allowed_root]))
    graph = AgentGraph(model_provider=FakeModelProvider(), tool_registry=registry)
    state = AgentRunState(
        session_id="session_test",
        run_id="run_test",
        user_text="/search note",
        mode="agent",
    )

    result = await graph.run(state)

    assert result.tool_calls == [{"name": "search_files", "args": {"query": "note", "limit": 10}}]
    assert result.tool_results[0]["summary"] == "找到 1 个文件"
    assert result.tool_results[0]["data"]["matches"] == [str(note)]
    assert result.messages[-1]["content"] == "找到 1 个文件"


@pytest.mark.asyncio
async def test_agent_graph_streams_assistant_and_tool_updates(tmp_allowed_root):
    note = tmp_allowed_root / "note.md"
    note.write_text("# DesktopMate\n", encoding="utf-8")
    registry = ToolRegistry()
    registry.register(SearchFilesTool(allowed_roots=[tmp_allowed_root]))
    graph = AgentGraph(model_provider=FakeModelProvider(), tool_registry=registry)
    state = AgentRunState(
        session_id="session_test",
        run_id="run_test",
        user_text="/search note",
        mode="agent",
    )

    updates = [update async for update in graph.stream(state)]

    assert [update.node for update in updates] == ["assistant", "tool"]
    assert updates[0].state.tool_calls == [
        {"name": "search_files", "args": {"query": "note", "limit": 10}}
    ]
    assert updates[1].state.tool_results[0]["summary"] == "找到 1 个文件"


@pytest.mark.asyncio
async def test_agent_graph_refuses_destructive_file_intent():
    graph = AgentGraph(model_provider=FakeModelProvider(), tool_registry=ToolRegistry())
    state = AgentRunState(
        session_id="session_test",
        run_id="run_test",
        user_text="删除整个桌面",
    )

    result = await graph.run(state)

    assert result.tool_calls == []
    assert result.messages[-1]["content"] == "当前不支持删除、移动或批量修改文件。"


@pytest.mark.asyncio
async def test_agent_graph_can_use_model_intent_router(tmp_allowed_root):
    note = tmp_allowed_root / "note.md"
    note.write_text("# DesktopMate\n", encoding="utf-8")
    registry = ToolRegistry()
    registry.register(SearchFilesTool(allowed_roots=[tmp_allowed_root]))
    intent_router = ModelIntentRouter(
        model_provider=FakeModelProvider(
            response_text=(
                '{"type":"tool_call","tool_name":"search_files",'
                '"tool_args":{"query":"note","limit":10},"confidence":0.94}'
            )
        )
    )
    graph = AgentGraph(
        model_provider=FakeModelProvider(),
        tool_registry=registry,
        intent_router=intent_router,
    )
    state = AgentRunState(
        session_id="session_test",
        run_id="run_test",
        user_text="帮我找一下 note 文件",
        mode="agent",
    )

    result = await graph.run(state)

    assert result.tool_calls == [{"name": "search_files", "args": {"query": "note", "limit": 10}}]
    assert result.messages[-1]["content"] == "找到 1 个文件"


@pytest.mark.asyncio
async def test_agent_graph_refuses_unregistered_tool_from_model_router():
    intent_router = ModelIntentRouter(
        model_provider=FakeModelProvider(
            response_text=(
                '{"type":"tool_call","tool_name":"delete_files",'
                '"tool_args":{"path":"~/Desktop"},"confidence":0.99}'
            )
        )
    )
    graph = AgentGraph(
        model_provider=FakeModelProvider(),
        tool_registry=ToolRegistry(),
        intent_router=intent_router,
    )
    state = AgentRunState(
        session_id="session_test",
        run_id="run_test",
        user_text="删除桌面文件",
    )

    result = await graph.run(state)

    assert result.tool_calls == []
    assert result.messages[-1]["content"] == "工具 delete_files 未注册或已禁用，不能执行该操作。"


@pytest.mark.asyncio
async def test_agent_graph_does_not_call_tool_when_intent_confidence_is_low(tmp_allowed_root):
    registry = ToolRegistry()
    registry.register(SearchFilesTool(allowed_roots=[tmp_allowed_root]))
    intent_router = ModelIntentRouter(
        model_provider=FakeModelProvider(
            response_text=(
                '{"type":"tool_call","tool_name":"search_files",'
                '"tool_args":{"query":"note","limit":10},"confidence":0.2}'
            )
        )
    )
    graph = AgentGraph(
        model_provider=FakeModelProvider(),
        tool_registry=registry,
        intent_router=intent_router,
    )
    state = AgentRunState(
        session_id="session_test",
        run_id="run_test",
        user_text="帮我处理一下 note",
    )

    result = await graph.run(state)

    assert result.tool_calls == []
    assert result.messages[-1]["content"] == "我还不确定要使用哪个工具，请再明确一下你的需求。"


@pytest.mark.asyncio
async def test_agent_graph_records_tool_error_as_state():
    registry = ToolRegistry()
    registry.register(FailingTool())
    intent_router = ModelIntentRouter(
        model_provider=FakeModelProvider(
            response_text=(
                '{"type":"tool_call","tool_name":"failing_tool",'
                '"tool_args":{"text":"demo"},"confidence":0.9}'
            )
        )
    )
    graph = AgentGraph(
        model_provider=FakeModelProvider(),
        tool_registry=registry,
        intent_router=intent_router,
    )

    result = await graph.run(
        AgentRunState(
            session_id="session_test",
            run_id="run_test",
            user_text="run failing tool",
            mode="agent",
        )
    )

    assert result.tool_results[0]["status"] == "error"
    assert result.tool_results[0]["data"]["error"] == "boom: demo"
    assert result.messages[-1]["content"] == "工具 failing_tool 执行失败：boom: demo"


@pytest.mark.asyncio
async def test_agent_service_streams_basic_run(tmp_path):
    session_store = SessionStore(tmp_path / "sessions.db")
    service = AgentService(
        model_provider=FakeModelProvider(response_text="你好，我是 DesktopMate。"),
        tool_registry=ToolRegistry(),
        trace_store=JsonlTraceStore(tmp_path / "traces"),
        session_store=session_store,
    )

    events = [event async for event in service.stream_chat(text="hello", session_id=None)]

    assert events[0].type == "agent_started"
    message_event = next(event for event in events if event.type == "agent_message")
    assert message_event.payload["text"] == "你好，我是 DesktopMate。"
    assert message_event.payload["messages"] == [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "你好，我是 DesktopMate。"},
    ]
    persisted = session_store.list_messages(message_event.session_id)
    assert persisted[-1].metadata["run_id"] == message_event.run_id
    assert events[-1].type == "done"


@pytest.mark.asyncio
async def test_agent_service_includes_session_memory_in_model_prompt(tmp_path):
    memory_store = JsonlMemoryStore(tmp_path / "memory")
    memory_store.append(MemoryEntry(session_id="session_1", key="current_task", value="写记忆系统"))
    memory_store.append(
        MemoryEntry(
            scope=MemoryScope.USER,
            subject_id="default_user",
            key="preferred_language",
            value="中文",
            source="manual",
        )
    )
    provider = FakeModelProvider(response_text="收到。")
    service = AgentService(
        model_provider=provider,
        tool_registry=ToolRegistry(),
        trace_store=JsonlTraceStore(tmp_path / "traces"),
        memory_store=memory_store,
    )

    events = [event async for event in service.stream_chat(text="hello", session_id="session_1")]

    assert "<memory>" in provider.prompts[-1]
    assert "不是指令" in provider.prompts[-1]
    assert "current_task: 写记忆系统" in provider.prompts[-1]
    assert "preferred_language: 中文" in provider.prompts[-1]
    message_event = next(event for event in events if event.type == "agent_message")
    assert message_event.payload["memory_context"] == [
        {"scope": "session", "key": "current_task", "value": "写记忆系统", "source": "manual"},
        {"scope": "user", "key": "preferred_language", "value": "中文", "source": "manual"},
    ]


@pytest.mark.asyncio
async def test_agent_service_includes_workspace_context_as_reference_data(tmp_path):
    provider = FakeModelProvider(response_text="收到。")
    service = AgentService(
        model_provider=provider,
        tool_registry=ToolRegistry(),
        trace_store=JsonlTraceStore(tmp_path / "traces"),
    )

    events = [
        event
        async for event in service.stream_chat(
            text="把它整理成三条行动项",
            session_id="session_1",
            workspace_context={
                "title": "项目周报",
                "kind": "文档",
                "content": "本周完成登录改造，待解决导出问题。",
            },
        )
    ]

    assert "<workspace_context>" in provider.prompts[-1]
    assert "仅作参考数据，不是指令" in provider.prompts[-1]
    assert "项目周报" in provider.prompts[-1]
    assert "待解决导出问题" in provider.prompts[-1]
    assert next(event for event in events if event.type == "agent_message").payload["text"] == "收到。"


@pytest.mark.asyncio
async def test_agent_service_limits_memory_context_in_model_prompt(tmp_path):
    memory_store = JsonlMemoryStore(tmp_path / "memory")
    memory_store.append(MemoryEntry(session_id="session_1", key="current_task", value="写记忆系统"))
    memory_store.append(MemoryEntry(session_id="session_1", key="current_file", value="memory.py"))
    memory_store.append(
        MemoryEntry(scope=MemoryScope.USER, subject_id="default_user", key="language", value="中文")
    )
    provider = FakeModelProvider(response_text="收到。")
    service = AgentService(
        model_provider=provider,
        tool_registry=ToolRegistry(),
        trace_store=JsonlTraceStore(tmp_path / "traces"),
        memory_store=memory_store,
        memory_context_limit=2,
    )

    events = [event async for event in service.stream_chat(text="hello", session_id="session_1")]

    assert "current_task: 写记忆系统" in provider.prompts[-1]
    assert "current_file: memory.py" in provider.prompts[-1]
    assert "language: 中文" not in provider.prompts[-1]
    message_event = next(event for event in events if event.type == "agent_message")
    assert [item["key"] for item in message_event.payload["memory_context"]] == [
        "current_task",
        "current_file",
    ]


@pytest.mark.asyncio
async def test_agent_service_ranks_memory_context_before_limiting(tmp_path):
    memory_store = JsonlMemoryStore(tmp_path / "memory")
    older = datetime(2026, 1, 1, tzinfo=UTC)
    newer = datetime(2026, 1, 2, tzinfo=UTC)
    memory_store.append(
        MemoryEntry(
            scope=MemoryScope.USER,
            subject_id="default_user",
            key="old_user_preference",
            value="旧偏好",
            confidence=1.0,
            updated_at=older,
        )
    )
    memory_store.append(
        MemoryEntry(
            scope=MemoryScope.USER,
            subject_id="default_user",
            key="new_user_preference",
            value="新偏好",
            confidence=0.4,
            updated_at=newer,
        )
    )
    memory_store.append(
        MemoryEntry(
            scope=MemoryScope.PROJECT,
            subject_id="desktopmate",
            key="project_stack",
            value="Vue + Python",
            confidence=0.4,
            updated_at=older,
        )
    )
    memory_store.append(
        MemoryEntry(
            session_id="session_1",
            key="current_task",
            value="实现记忆排序",
            confidence=0.4,
            updated_at=older,
        )
    )
    provider = FakeModelProvider(response_text="收到。")
    service = AgentService(
        model_provider=provider,
        tool_registry=ToolRegistry(),
        trace_store=JsonlTraceStore(tmp_path / "traces"),
        memory_store=memory_store,
        memory_project_id="desktopmate",
        memory_context_limit=3,
    )

    events = [event async for event in service.stream_chat(text="hello", session_id="session_1")]

    message_event = next(event for event in events if event.type == "agent_message")
    assert [item["key"] for item in message_event.payload["memory_context"]] == [
        "current_task",
        "project_stack",
        "new_user_preference",
    ]


@pytest.mark.asyncio
async def test_agent_service_can_remember_session_fact(tmp_path):
    memory_store = JsonlMemoryStore(tmp_path / "memory")
    service = AgentService(
        model_provider=FakeModelProvider(),
        tool_registry=ToolRegistry(),
        trace_store=JsonlTraceStore(tmp_path / "traces"),
        memory_store=memory_store,
    )

    events = [
        event
        async for event in service.stream_chat(
            text="/remember session current_task 写记忆系统",
            session_id="session_1",
        )
    ]

    entries = memory_store.read_session("session_1")
    assert len(entries) == 1
    assert entries[0].scope == MemoryScope.SESSION
    assert entries[0].key == "current_task"
    assert entries[0].value == "写记忆系统"
    message_event = next(event for event in events if event.type == "agent_message")
    assert message_event.payload["text"] == "已记住 current_task。"


@pytest.mark.asyncio
async def test_agent_service_can_remember_user_fact(tmp_path):
    memory_store = JsonlMemoryStore(tmp_path / "memory")
    service = AgentService(
        model_provider=FakeModelProvider(),
        tool_registry=ToolRegistry(),
        trace_store=JsonlTraceStore(tmp_path / "traces"),
        memory_store=memory_store,
    )

    events = [
        event
        async for event in service.stream_chat(
            text="/remember preferred_language 中文",
            session_id="session_1",
        )
    ]

    entries = memory_store.read_scope(MemoryScope.USER, "default_user")
    assert len(entries) == 1
    assert entries[0].key == "preferred_language"
    assert entries[0].value == "中文"
    assert entries[0].source == "user_command"
    message_event = next(event for event in events if event.type == "agent_message")
    assert message_event.payload["text"] == "已记住 preferred_language。"


@pytest.mark.asyncio
async def test_agent_service_updates_existing_memory_key(tmp_path):
    memory_store = JsonlMemoryStore(tmp_path / "memory")
    service = AgentService(
        model_provider=FakeModelProvider(),
        tool_registry=ToolRegistry(),
        trace_store=JsonlTraceStore(tmp_path / "traces"),
        memory_store=memory_store,
    )

    async for _ in service.stream_chat(
        text="/remember preferred_language 中文",
        session_id="session_1",
    ):
        pass
    events = [
        event
        async for event in service.stream_chat(
            text="/remember preferred_language English",
            session_id="session_1",
        )
    ]

    entries = memory_store.read_scope(MemoryScope.USER, "default_user")
    assert len(entries) == 1
    assert entries[0].key == "preferred_language"
    assert entries[0].value == "English"
    message_event = next(event for event in events if event.type == "agent_message")
    assert message_event.payload["text"] == "已更新 preferred_language。"


@pytest.mark.asyncio
async def test_agent_service_creates_memory_candidate_without_writing(tmp_path):
    memory_store = JsonlMemoryStore(tmp_path / "memory")
    service = AgentService(
        model_provider=FakeModelProvider(),
        tool_registry=ToolRegistry(),
        trace_store=JsonlTraceStore(tmp_path / "traces"),
        memory_store=memory_store,
    )

    events = [
        event
        async for event in service.stream_chat(
            text="/remember candidate preferred_language 中文",
            session_id="session_1",
        )
    ]

    assert memory_store.read_scope(MemoryScope.USER, "default_user") == []
    message_event = next(event for event in events if event.type == "agent_message")
    assert "候选记忆" in message_event.payload["text"]
    assert "preferred_language" in message_event.payload["text"]


@pytest.mark.asyncio
async def test_agent_service_approves_memory_candidate(tmp_path):
    memory_store = JsonlMemoryStore(tmp_path / "memory")
    service = AgentService(
        model_provider=FakeModelProvider(),
        tool_registry=ToolRegistry(),
        trace_store=JsonlTraceStore(tmp_path / "traces"),
        memory_store=memory_store,
    )

    candidate_events = [
        event
        async for event in service.stream_chat(
            text="/remember candidate preferred_language 中文",
            session_id="session_1",
        )
    ]
    candidate_message = next(event for event in candidate_events if event.type == "agent_message")
    candidate_id = candidate_message.payload["text"].split(" ")[1].split("：", maxsplit=1)[0]
    approved_events = [
        event
        async for event in service.stream_chat(
            text=f"/remember approve {candidate_id}",
            session_id="session_1",
        )
    ]

    entries = memory_store.read_scope(MemoryScope.USER, "default_user")
    assert len(entries) == 1
    assert entries[0].key == "preferred_language"
    assert entries[0].value == "中文"
    assert entries[0].source == "candidate_approved"
    message_event = next(event for event in approved_events if event.type == "agent_message")
    assert message_event.payload["text"] == "已记住 preferred_language。"


@pytest.mark.asyncio
async def test_agent_service_can_remember_project_fact(tmp_path):
    memory_store = JsonlMemoryStore(tmp_path / "memory")
    service = AgentService(
        model_provider=FakeModelProvider(),
        tool_registry=ToolRegistry(),
        trace_store=JsonlTraceStore(tmp_path / "traces"),
        memory_store=memory_store,
        memory_project_id="desktopmate",
    )

    events = [
        event
        async for event in service.stream_chat(
            text="/remember project stack Vue + Python",
            session_id="session_1",
        )
    ]

    entries = memory_store.read_scope(MemoryScope.PROJECT, "desktopmate")
    assert len(entries) == 1
    assert entries[0].key == "stack"
    assert entries[0].value == "Vue + Python"
    message_event = next(event for event in events if event.type == "agent_message")
    assert message_event.payload["text"] == "已记住 stack。"


@pytest.mark.asyncio
async def test_agent_service_rejects_incomplete_remember_command(tmp_path):
    service = AgentService(
        model_provider=FakeModelProvider(),
        tool_registry=ToolRegistry(),
        trace_store=JsonlTraceStore(tmp_path / "traces"),
        memory_store=JsonlMemoryStore(tmp_path / "memory"),
    )

    events = [
        event
        async for event in service.stream_chat(text="/remember only_key", session_id="session_1")
    ]

    message_event = next(event for event in events if event.type == "agent_message")
    assert message_event.payload["text"] == "记忆需要 key 和 value。"


@pytest.mark.asyncio
async def test_agent_service_rejects_sensitive_memory(tmp_path):
    memory_store = JsonlMemoryStore(tmp_path / "memory")
    service = AgentService(
        model_provider=FakeModelProvider(),
        tool_registry=ToolRegistry(),
        trace_store=JsonlTraceStore(tmp_path / "traces"),
        memory_store=memory_store,
    )

    events = [
        event
        async for event in service.stream_chat(
            text="/remember api_key sk-secret-value",
            session_id="session_1",
        )
    ]

    assert memory_store.read_scope(MemoryScope.USER, "default_user") == []
    message_event = next(event for event in events if event.type == "agent_message")
    assert message_event.payload["text"] == "这条内容看起来包含敏感信息，未写入记忆。"


@pytest.mark.asyncio
async def test_agent_service_lists_current_memory_context(tmp_path):
    memory_store = JsonlMemoryStore(tmp_path / "memory")
    memory_store.append(MemoryEntry(session_id="session_1", key="current_task", value="写记忆系统"))
    memory_store.append(
        MemoryEntry(scope=MemoryScope.USER, subject_id="default_user", key="language", value="中文")
    )
    service = AgentService(
        model_provider=FakeModelProvider(),
        tool_registry=ToolRegistry(),
        trace_store=JsonlTraceStore(tmp_path / "traces"),
        memory_store=memory_store,
    )

    events = [event async for event in service.stream_chat(text="/memories", session_id="session_1")]

    message_event = next(event for event in events if event.type == "agent_message")
    assert message_event.payload["text"] == "- session.current_task: 写记忆系统\n- user.language: 中文"


@pytest.mark.asyncio
async def test_agent_service_forgets_memory_by_scope_and_key(tmp_path):
    memory_store = JsonlMemoryStore(tmp_path / "memory")
    memory_store.append(MemoryEntry(scope=MemoryScope.USER, subject_id="default_user", key="language", value="中文"))
    service = AgentService(
        model_provider=FakeModelProvider(),
        tool_registry=ToolRegistry(),
        trace_store=JsonlTraceStore(tmp_path / "traces"),
        memory_store=memory_store,
    )

    events = [
        event
        async for event in service.stream_chat(text="/forget user language", session_id="session_1")
    ]

    assert memory_store.read_scope(MemoryScope.USER, "default_user") == []
    message_event = next(event for event in events if event.type == "agent_message")
    assert message_event.payload["text"] == "已删除 1 条记忆。"


@pytest.mark.asyncio
async def test_agent_service_streams_tool_call_payload(tmp_path, tmp_allowed_root):
    note = tmp_allowed_root / "note.md"
    note.write_text("# DesktopMate\n", encoding="utf-8")
    registry = ToolRegistry()
    registry.register(SearchFilesTool(allowed_roots=[tmp_allowed_root]))
    service = AgentService(
        model_provider=FakeModelProvider(),
        tool_registry=registry,
        trace_store=JsonlTraceStore(tmp_path),
    )

    events = [event async for event in service.stream_chat(text="/search note", session_id=None, mode="agent")]

    assert [event.type for event in events] == [
        "agent_started",
        "tool_call_started",
        "tool_call_finished",
        "agent_message",
        "done",
    ]
    assert events[1].payload["tool_name"] == "search_files"
    assert events[1].payload["args"] == {"query": "note", "limit": 10}
    assert events[1].payload["risk_level"] == "read"
    assert events[1].payload["requires_permission"] is False
    assert str(events[1].payload["tool_call_id"]).startswith("tool_run_")
    assert events[2].payload["tool_name"] == "search_files"
    assert events[2].payload["result"]["summary"] == "找到 1 个文件"
    message_event = next(event for event in events if event.type == "agent_message")
    tool_call = message_event.payload["tool_calls"][0]
    assert tool_call["name"] == "search_files"
    assert tool_call["args"] == {"query": "note", "limit": 10}
    assert tool_call["risk_level"] == "read"
    assert tool_call["requires_permission"] is False
    assert str(tool_call["id"]).startswith("tool_run_")
    assert message_event.payload["tool_results"][0]["summary"] == "找到 1 个文件"


@pytest.mark.asyncio
async def test_agent_service_routes_natural_language_search_to_tool(tmp_path, tmp_allowed_root):
    note = tmp_allowed_root / "note.md"
    note.write_text("# DesktopMate\n", encoding="utf-8")
    registry = ToolRegistry()
    registry.register(SearchFilesTool(allowed_roots=[tmp_allowed_root]))
    service = AgentService(
        model_provider=FakeModelProvider(),
        tool_registry=registry,
        trace_store=JsonlTraceStore(tmp_path),
    )

    events = [event async for event in service.stream_chat(text="帮我找一下 note 文件", session_id=None, mode="agent")]

    message_event = next(event for event in events if event.type == "agent_message")
    tool_call = message_event.payload["tool_calls"][0]
    assert tool_call["name"] == "search_files"
    assert tool_call["args"] == {"query": "note", "limit": 10}
    assert tool_call["risk_level"] == "read"
    assert message_event.payload["text"] == "找到 1 个文件"


@pytest.mark.asyncio
async def test_agent_service_streams_tool_failure_event(tmp_path):
    registry = ToolRegistry()
    registry.register(FailingTool())
    intent_router = ModelIntentRouter(
        model_provider=FakeModelProvider(
            response_text=(
                '{"type":"tool_call","tool_name":"failing_tool",'
                '"tool_args":{"text":"demo"},"confidence":0.9}'
            )
        )
    )
    service = AgentService(
        model_provider=FakeModelProvider(),
        tool_registry=registry,
        trace_store=JsonlTraceStore(tmp_path),
        intent_router=intent_router,
    )

    events = [event async for event in service.stream_chat(text="run failing tool", session_id=None, mode="agent")]

    assert [event.type for event in events] == [
        "agent_started",
        "tool_call_started",
        "tool_call_failed",
        "agent_message",
        "done",
    ]
    assert events[2].payload["tool_name"] == "failing_tool"
    assert events[2].payload["error"] == "boom: demo"
    assert events[3].payload["text"] == "工具 failing_tool 执行失败：boom: demo"


def test_agent_service_streams_tool_validation_failure_event(tmp_path):
    async def run() -> None:
        registry = ToolRegistry()
        registry.register(FailingTool())
        intent_router = ModelIntentRouter(
            model_provider=FakeModelProvider(
                response_text=(
                    '{"type":"tool_call","tool_name":"failing_tool",'
                    '"tool_args":{},"confidence":0.9}'
                )
            )
        )
        service = AgentService(
            model_provider=FakeModelProvider(),
            tool_registry=registry,
            trace_store=JsonlTraceStore(tmp_path),
            intent_router=intent_router,
        )

        events = [
            event
            async for event in service.stream_chat(
                text="run failing tool", session_id=None, mode="agent"
            )
        ]

        assert [event.type for event in events] == [
            "agent_started",
            "tool_call_started",
            "tool_call_failed",
            "agent_message",
            "done",
        ]
        assert events[2].payload["error_kind"] == "validation_error"
        assert "参数无效" in events[3].payload["text"]

    asyncio.run(run())


@pytest.mark.asyncio
async def test_agent_service_waits_for_permission_before_protected_tool(tmp_path):
    from desktopmate_agent.tools.permissions import PermissionDecision, PermissionRequestBroker

    broker = PermissionRequestBroker(timeout_seconds=1)
    registry = ToolRegistry()
    registry.register(ProtectedTool())
    intent_router = ModelIntentRouter(
        model_provider=FakeModelProvider(
            response_text=(
                '{"type":"tool_call","tool_name":"protected_tool",'
                '"tool_args":{"text":"demo"},"confidence":0.9}'
            )
        )
    )
    service = AgentService(
        model_provider=FakeModelProvider(),
        tool_registry=registry,
        trace_store=JsonlTraceStore(tmp_path),
        intent_router=intent_router,
        permission_broker=broker,
    )

    stream = service.stream_chat(text="run protected tool", session_id=None, mode="agent")
    first = await anext(stream)
    permission_event = await anext(stream)
    broker.resolve(
        permission_event.payload["request_id"],
        PermissionDecision(decision="approved"),
    )
    remaining = [event async for event in stream]
    events = [first, permission_event, *remaining]

    assert [event.type for event in events] == [
        "agent_started",
        "permission_request",
        "tool_call_started",
        "tool_call_finished",
        "agent_message",
        "done",
    ]
    assert permission_event.payload["tool_name"] == "protected_tool"
    assert events[4].payload["text"] == "已执行：demo"


@pytest.mark.asyncio
async def test_agent_service_stops_when_permission_is_denied(tmp_path):
    from desktopmate_agent.tools.permissions import PermissionDecision, PermissionRequestBroker

    broker = PermissionRequestBroker(timeout_seconds=1)
    registry = ToolRegistry()
    registry.register(ProtectedTool())
    intent_router = ModelIntentRouter(
        model_provider=FakeModelProvider(
            response_text=(
                '{"type":"tool_call","tool_name":"protected_tool",'
                '"tool_args":{"text":"demo"},"confidence":0.9}'
            )
        )
    )
    session_store = SessionStore(tmp_path / "sessions.db")
    service = AgentService(
        model_provider=FakeModelProvider(),
        tool_registry=registry,
        trace_store=JsonlTraceStore(tmp_path / "traces"),
        intent_router=intent_router,
        permission_broker=broker,
        session_store=session_store,
    )

    stream = service.stream_chat(text="run protected tool", session_id=None, mode="agent")
    first = await anext(stream)
    permission_event = await anext(stream)
    broker.resolve(
        permission_event.payload["request_id"],
        PermissionDecision(decision="denied", reason="用户拒绝"),
    )
    remaining = [event async for event in stream]
    events = [first, permission_event, *remaining]

    assert [event.type for event in events] == [
        "agent_started",
        "permission_request",
        "agent_message",
        "done",
    ]
    assert events[2].payload["text"] == "已取消执行 protected_tool：用户拒绝"
    persisted = session_store.list_messages(events[2].session_id)
    assert persisted[-1].metadata["run_id"] == events[2].run_id
