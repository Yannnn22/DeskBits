import asyncio

import pytest

from desktopmate_agent.agent.service import AgentService
from desktopmate_agent.knowledge.store import DocumentStore
from desktopmate_agent.memory.models import MemoryScope
from desktopmate_agent.memory.store import JsonlMemoryStore, SqliteMemoryStore
from desktopmate_agent.tools.registry import ToolRegistry
from desktopmate_agent.trace.store import JsonlTraceStore


class ScriptedProvider:
    def __init__(self, *, normal_response: str = "收到。", scripted: dict[str, str]) -> None:
        self.normal_response = normal_response
        self.scripted = scripted
        self.prompts: list[str] = []

    async def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        for marker, response in self.scripted.items():
            if marker in prompt:
                return response
        return self.normal_response


@pytest.mark.asyncio
async def test_agent_service_writes_session_summary_after_threshold(tmp_path):
    memory_store = JsonlMemoryStore(tmp_path / "memory")
    provider = ScriptedProvider(
        scripted={"会话压缩器": "用户讨论了记忆系统和 RAG 测试。"}
    )
    service = AgentService(
        model_provider=provider,
        tool_registry=ToolRegistry(),
        trace_store=JsonlTraceStore(tmp_path / "traces"),
        memory_store=memory_store,
        context_compression_enabled=True,
        context_compression_threshold=3,
    )

    for text in ("第一条消息", "第二条消息", "第三条消息"):
        events = [
            event async for event in service.stream_chat(text=text, session_id="session_1")
        ]

    entries = memory_store.read_scope(MemoryScope.SESSION, "session_1")
    summary = next(entry for entry in entries if entry.key == "session_summary")
    assert summary.value == "用户讨论了记忆系统和 RAG 测试。"
    assert summary.source == "auto_summary"
    assert events[-1].type == "done"
    assert any("session_summary" in event.payload for event in events if event.type == "agent_message")
    assert any("会话压缩器" in prompt for prompt in provider.prompts)


@pytest.mark.asyncio
async def test_agent_service_extracts_model_memory_candidates(tmp_path):
    memory_store = JsonlMemoryStore(tmp_path / "memory")
    provider = ScriptedProvider(
        scripted={
            "记忆抽取器": (
                '[{"scope": "user", "key": "notes_app", "value": "Obsidian"},'
                '{"scope": "session", "key": "current_task", "value": "测试记忆"}]'
            )
        }
    )
    service = AgentService(
        model_provider=provider,
        tool_registry=ToolRegistry(),
        trace_store=JsonlTraceStore(tmp_path / "traces"),
        memory_store=memory_store,
        auto_memory_candidates=True,
        auto_memory_candidates_limit=3,
    )

    events = [
        event
        async for event in service.stream_chat(
            text="我平时用 Obsidian 记笔记，现在在测试记忆",
            session_id="session_1",
        )
    ]
    message = next(event for event in events if event.type == "agent_message")
    candidates = message.payload["memory_candidates"]

    assert len(candidates) == 2
    assert candidates[0]["candidate_id"].startswith("mem_")
    assert {key: value for key, value in candidates[0].items() if key != "candidate_id"} == {
        "scope": "user",
        "key": "notes_app",
        "value": "Obsidian",
    }
    assert memory_store.read_scope(MemoryScope.USER, "default_user") == []

    approved = [
        event
        async for event in service.stream_chat(
            text=f"/remember approve {candidates[0]['candidate_id']}",
            session_id="session_1",
        )
    ]
    assert next(e for e in approved if e.type == "agent_message").payload["text"] == (
        "已记住 notes_app。"
    )
    entries = memory_store.read_scope(MemoryScope.USER, "default_user")
    assert entries[0].key == "notes_app"
    assert entries[0].value == "Obsidian"


@pytest.mark.asyncio
async def test_agent_service_ignores_invalid_candidate_json(tmp_path):
    memory_store = JsonlMemoryStore(tmp_path / "memory")
    service = AgentService(
        model_provider=ScriptedProvider(scripted={"记忆抽取器": "这不是 JSON"}),
        tool_registry=ToolRegistry(),
        trace_store=JsonlTraceStore(tmp_path / "traces"),
        memory_store=memory_store,
        auto_memory_candidates=True,
    )

    events = [
        event async for event in service.stream_chat(text="我喜欢收集资料", session_id="session_1")
    ]

    message = next(event for event in events if event.type == "agent_message")
    assert "memory_candidates" not in message.payload
    assert memory_store.read_scope(MemoryScope.USER, "default_user") == []


@pytest.mark.asyncio
async def test_sqlite_memory_candidate_survives_service_restart(tmp_path):
    store = SqliteMemoryStore(tmp_path / "desktopmate.db")
    first = AgentService(
        model_provider=ScriptedProvider(
            scripted={"记忆抽取器": '[{"scope":"user","key":"editor","value":"Obsidian"}]'}
        ),
        tool_registry=ToolRegistry(),
        trace_store=JsonlTraceStore(tmp_path / "traces"),
        memory_store=store,
        auto_memory_candidates=True,
    )
    events = [event async for event in first.stream_chat(text="我常用 Obsidian", session_id="session_1")]
    candidate_id = next(event for event in events if event.type == "agent_message").payload["memory_candidates"][0]["candidate_id"]
    assert store.get_candidate(candidate_id, session_id="session_1") is not None

    restarted = AgentService(
        model_provider=ScriptedProvider(scripted={}),
        tool_registry=ToolRegistry(),
        trace_store=JsonlTraceStore(tmp_path / "traces"),
        memory_store=store,
    )
    approved = [
        event async for event in restarted.stream_chat(
            text=f"/remember approve {candidate_id}",
            session_id="session_1",
        )
    ]

    assert next(event for event in approved if event.type == "agent_message").payload["text"] == "已记住 editor。"
    assert store.read_scope(MemoryScope.USER, "default_user")[0].value == "Obsidian"


# --- D4: 工具轮次也压缩/抽取，transcript 过滤工具摘要 ---


def test_transcript_skips_tool_summary_messages(tmp_path):
    """带 tool_summary 标记的消息在 skip 模式下被过滤。"""
    from desktopmate_agent.agent.service import AgentService

    service = AgentService(
        model_provider=ScriptedProvider(scripted={}),
        tool_registry=ToolRegistry(),
        trace_store=JsonlTraceStore(tmp_path / "traces"),
    )
    messages = [
        {"role": "user", "content": "帮我搜下文件"},
        {"role": "assistant", "content": "找到 3 个文件", "tool_summary": True},
        {"role": "assistant", "content": "这是完整回答"},
    ]
    full = service._transcript(messages)
    assert "找到 3 个文件" in full  # 默认不过滤

    filtered = service._transcript(messages, skip_tool_summaries=True)
    assert "找到 3 个文件" not in filtered  # 工具摘要被过滤
    assert "帮我搜下文件" in filtered
    assert "这是完整回答" in filtered


def test_repeated_memory_candidate_auto_consolidates_to_knowledge(tmp_path):
    memory_store = JsonlMemoryStore(tmp_path / "memory")
    knowledge_store = DocumentStore(tmp_path / "desktopmate.db")
    provider = ScriptedProvider(
        scripted={
            "记忆抽取器": '[{"scope":"user","key":"favorite_editor","value":"Cursor"}]',
        }
    )
    service = AgentService(
        model_provider=provider,
        tool_registry=ToolRegistry(),
        trace_store=JsonlTraceStore(tmp_path / "traces"),
        memory_store=memory_store,
        auto_memory_candidates=True,
        knowledge_store=knowledge_store,
    )

    async def run_turns(count: int) -> None:
        for index in range(count):
            _ = [
                event
                async for event in service.stream_chat(
                    text=f"第 {index + 1} 次说明我喜欢 Cursor",
                    session_id="session_1",
                )
            ]

    asyncio.run(run_turns(3))

    docs = knowledge_store.list_documents()
    assert len(docs) == 1
    assert docs[0].source == "auto_consolidation"
    assert docs[0].importance == 0.7
    assert knowledge_store.list_chunks(docs[0].id)[0]["text"] == "favorite_editor: Cursor"

    asyncio.run(run_turns(1))
    assert len(knowledge_store.list_documents()) == 1


@pytest.mark.asyncio
async def test_tool_round_also_compresses_and_filters_noise(tmp_path):
    """工具调用后的轮次也触发压缩，且压缩 prompt 不含工具摘要。"""
    memory_store = JsonlMemoryStore(tmp_path / "memory")
    provider = ScriptedProvider(scripted={"会话压缩器": "用户搜索了文件。"})
    service = AgentService(
        model_provider=provider,
        tool_registry=ToolRegistry(),
        trace_store=JsonlTraceStore(tmp_path / "traces"),
        memory_store=memory_store,
        context_compression_enabled=True,
        context_compression_threshold=1,  # 每轮都压缩
    )

    _ = [
        event
        async for event in service.stream_chat(
            text="帮我搜下文件", session_id="session_1"
        )
    ]
    entries = memory_store.read_scope(MemoryScope.SESSION, "session_1")
    summary = next(entry for entry in entries if entry.key == "session_summary")
    assert summary.value == "用户搜索了文件。"
    # 压缩 prompt 不含工具摘要噪音
    compression_prompt = next(p for p in provider.prompts if "会话压缩器" in p)
    assert "工具" not in compression_prompt.split("<conversation>")[1].split("</conversation>")[0]
