"""对话内知识保存（记住/保存到知识库）+ 未找到兜底。"""

import asyncio
from pathlib import Path

from desktopmate_agent.agent.intent import HeuristicIntentRouter, IntentType
from desktopmate_agent.config import Settings
from desktopmate_agent.knowledge.service import DocumentService
from desktopmate_agent.knowledge.store import DocumentStore
from desktopmate_agent.rag.embeddings import FakeEmbeddingProvider
from desktopmate_agent.tools.knowledge_tools import (
    KnowledgeSaveArgs,
    KnowledgeSearchArgs,
    KnowledgeSearchTool,
    KnowledgeSaveTool,
)

AUTH_HEADERS = {"X-DesktopMate-Token": "test-token"}
EID = "fake:text-embedding-3-small"


def make_settings(tmp_path: Path, **overrides) -> Settings:
    defaults = {
        "trace_dir": tmp_path / "traces",
        "settings_dir": tmp_path / "settings",
        "app_data_dir": tmp_path,
        "knowledge_dir": tmp_path / "knowledge",
        "knowledge_db_path": tmp_path / "desktopmate.db",
        "session_db_path": tmp_path / "desktopmate.db",
        "auth_token": "test-token",
        "openai_api_key": None,
        "deepseek_api_key": None,
        "embedding_provider": "fake",
        "embedding_dimensions": 16,
    }
    defaults.update(overrides)
    return Settings(**defaults)


def build_service(tmp_path: Path) -> DocumentService:
    settings = make_settings(tmp_path)
    provider = FakeEmbeddingProvider(dimensions=16)
    store = DocumentStore(settings.knowledge_db_path)
    tool = KnowledgeSearchTool(
        knowledge_root=settings.knowledge_dir,
        embedding_provider=provider,
        embedding_dimensions=16,
        document_store=store,
        embedding_id=EID,
    )
    service = DocumentService(
        store=store,
        originals_dir=settings.knowledge_dir / "originals",
        extracted_dir=settings.knowledge_dir / "extracted",
        search_tool=tool,
        embedding_provider=provider,
        embedding_id=EID,
    )
    return service


# --- save_chat_note ---


def test_save_chat_note_creates_searchable_document(tmp_path):
    service = build_service(tmp_path)
    doc_id = service.save_chat_note(title="服务器IP", content="我的服务器 IP 是 10.0.0.8，位于机房 A。")

    document = service.store.get_document(doc_id)
    assert document is not None
    assert document.status == "indexed"
    collection = service.store.get_collection(document.collection_id)
    assert collection is not None
    assert collection.name == "对话保存"

    # 立即可检索
    result = asyncio.run(service.search_tool.run(KnowledgeSearchArgs(query="服务器 IP", limit=3)))
    matches = result.data["matches"]
    assert any("10.0.0.8" in m["text"] for m in matches)


def test_save_chat_note_idempotent(tmp_path):
    service = build_service(tmp_path)
    first = service.save_chat_note(title="", content="重复内容：会议改到周三。")
    second = service.save_chat_note(title="", content="重复内容：会议改到周三。")
    assert first == second
    docs = service.store.list_documents()
    assert len(docs) == 1


# --- intent 路由 ---


def test_intent_routes_save_commands():
    router = HeuristicIntentRouter()

    slash = router.route("/save 我的服务器 IP 是 10.0.0.8")
    assert slash.type == IntentType.TOOL_CALL
    assert slash.tool_name == "knowledge_save"
    assert slash.tool_args["content"] == "我的服务器 IP 是 10.0.0.8"
    assert slash.tool_args["title"] == "我的服务器 IP 是 10.0.0.8"

    natural = router.route("记住：我的服务器 IP 是 10.0.0.8")
    assert natural.type == IntentType.TOOL_CALL
    assert natural.tool_name == "knowledge_save"
    assert natural.tool_args["content"] == "我的服务器 IP 是 10.0.0.8"

    kb = router.route("帮我保存到知识库：会议改到周三下午三点")
    assert kb.type == IntentType.TOOL_CALL
    assert kb.tool_name == "knowledge_save"
    assert kb.tool_args["content"] == "会议改到周三下午三点"

    plain = router.route("你好")
    assert plain.type != IntentType.TOOL_CALL or plain.tool_name != "knowledge_save"


# --- 工具层 ---


def test_knowledge_save_tool(tmp_path):
    service = build_service(tmp_path)
    tool = KnowledgeSaveTool(saver=service.save_chat_note)
    result = asyncio.run(
        tool.run(KnowledgeSaveArgs(title="测试", content="这是一条测试知识"))
    )
    assert result.status == "success"
    assert "已保存到知识库" in result.summary
    assert result.data["document_id"]


# --- 端到端：保存 → 检索（流式 + 权限批准，模拟前端批准流） ---


def test_stream_save_then_ask(tmp_path):
    from desktopmate_agent.agent.service import AgentService
    from desktopmate_agent.models.provider import FakeModelProvider
    from desktopmate_agent.sessions.store import SessionStore
    from desktopmate_agent.tools.permissions import PermissionDecision, PermissionRequestBroker
    from desktopmate_agent.tools.registry import ToolRegistry
    from desktopmate_agent.trace.store import JsonlTraceStore

    settings = make_settings(tmp_path)
    provider = FakeEmbeddingProvider(dimensions=16)
    store = DocumentStore(settings.knowledge_db_path)
    search_tool = KnowledgeSearchTool(
        knowledge_root=settings.knowledge_dir,
        embedding_provider=provider,
        embedding_dimensions=16,
        document_store=store,
        embedding_id=EID,
    )
    registry = ToolRegistry()
    registry.register(search_tool)
    document_service = DocumentService(
        store=store,
        originals_dir=settings.knowledge_dir / "originals",
        extracted_dir=settings.knowledge_dir / "extracted",
        search_tool=search_tool,
        embedding_provider=provider,
        embedding_id=EID,
    )
    registry.register(KnowledgeSaveTool(saver=document_service.save_chat_note))
    broker = PermissionRequestBroker(timeout_seconds=5)
    service = AgentService(
        model_provider=FakeModelProvider(response_text="收到。"),
        tool_registry=registry,
        trace_store=JsonlTraceStore(tmp_path / "traces"),
        intent_router=HeuristicIntentRouter(),
        session_store=SessionStore(tmp_path / "sessions.db"),
        permission_broker=broker,
    )

    async def run(text: str, session_id: str, mode: str = "chat"):
        events = []
        async for event in service.stream_chat(text=text, session_id=session_id, mode=mode):
            events.append(event)
            if event.type == "permission_request":
                broker.resolve(
                    event.payload["request_id"],
                    PermissionDecision(decision="approved", reason="test"),
                )
        return events

    events = asyncio.run(run("记住：我的服务器 IP 是 10.0.0.8", "s1", mode="agent"))
    assert any(e.type == "agent_message" for e in events)
    docs = store.list_documents()
    assert len(docs) == 1
    assert docs[0].status == "indexed"

    # 保存后立即检索可命中
    result = asyncio.run(search_tool.run(KnowledgeSearchArgs(query="服务器 IP", limit=3)))
    assert any("10.0.0.8" in m["text"] for m in result.data["matches"])


# --- 未找到兜底：prompt 含 knowledge_miss 指令 ---


def test_knowledge_miss_instruction_in_prompt(tmp_path):
    from desktopmate_agent.agent.service import AgentService
    from desktopmate_agent.models.provider import FakeModelProvider
    from desktopmate_agent.sessions.store import SessionStore
    from desktopmate_agent.tools.registry import ToolRegistry
    from desktopmate_agent.trace.store import JsonlTraceStore

    # 空知识库 + 注册了 knowledge_search（检索 0 命中 → knowledge_miss）
    settings = make_settings(tmp_path)
    provider = FakeEmbeddingProvider(dimensions=16)
    store = DocumentStore(settings.knowledge_db_path)
    search_tool = KnowledgeSearchTool(
        knowledge_root=settings.knowledge_dir,
        embedding_provider=provider,
        embedding_dimensions=16,
        document_store=store,
        embedding_id=EID,
    )
    registry = ToolRegistry()
    registry.register(search_tool)
    fake = FakeModelProvider(response_text="收到。")
    service = AgentService(
        model_provider=fake,
        tool_registry=registry,
        trace_store=JsonlTraceStore(tmp_path / "traces"),
        intent_router=HeuristicIntentRouter(),
        session_store=SessionStore(tmp_path / "sessions.db"),
    )

    async def run():
        events = []
        async for event in service.stream_chat(text="我的知识库里有量子桂花树吗", session_id="s1"):
            events.append(event)
        return events

    events = asyncio.run(run())
    assert any(e.type == "agent_message" for e in events)
    assert fake.prompts, "fake 模型应收到 prompt"
    assert any("知识库检索结果" in p and "没有找到" in p for p in fake.prompts)
