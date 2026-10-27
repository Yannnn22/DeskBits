"""knowledge_summarize：对话中「总结该文档」直接取文档内容。"""

import asyncio
from pathlib import Path

from desktopmate_agent.agent.intent import HeuristicIntentRouter, IntentType
from desktopmate_agent.config import Settings
from desktopmate_agent.knowledge.service import DocumentService
from desktopmate_agent.knowledge.store import DocumentStore
from desktopmate_agent.rag.embeddings import FakeEmbeddingProvider
from desktopmate_agent.tools.knowledge_tools import (
    KnowledgeSearchTool,
    KnowledgeSummarizeArgs,
    KnowledgeSummarizeTool,
)

EID = "fake:text-embedding-3-small"
DOC1 = "# 量子桂花树\n量子桂花树是验证 RAG 闭环的独特名词，批次 1785826048。每年八月开花，花期两周。"
DOC2 = "# 服务器手册\n机房 A 的服务器 IP 是 10.0.0.8，每周三凌晨 3 点备份。"


def build_service(tmp_path: Path) -> DocumentService:
    settings = Settings(
        trace_dir=tmp_path / "traces",
        settings_dir=tmp_path / "settings",
        app_data_dir=tmp_path,
        knowledge_dir=tmp_path / "knowledge",
        knowledge_db_path=tmp_path / "desktopmate.db",
        session_db_path=tmp_path / "desktopmate.db",
        auth_token="t",
        openai_api_key=None,
        deepseek_api_key=None,
        embedding_provider="fake",
        embedding_dimensions=16,
    )
    provider = FakeEmbeddingProvider(dimensions=16)
    store = DocumentStore(settings.knowledge_db_path)
    tool = KnowledgeSearchTool(
        knowledge_root=settings.knowledge_dir,
        embedding_provider=provider,
        embedding_dimensions=16,
        document_store=store,
        embedding_id=EID,
    )
    return DocumentService(
        store=store,
        originals_dir=settings.knowledge_dir / "originals",
        extracted_dir=settings.knowledge_dir / "extracted",
        search_tool=tool,
        embedding_provider=provider,
        embedding_id=EID,
    )


def test_summarize_defaults_to_most_recent(tmp_path):
    service = build_service(tmp_path)
    service.upload(filename="quantum.md", content=DOC1.encode())
    service.upload(filename="server.md", content=DOC2.encode())

    matches = service.summarize_document()
    assert matches
    assert matches[0]["title"] == "server.md"  # 最近上传
    assert "10.0.0.8" in matches[0]["text"]


def test_summarize_named_subject(tmp_path):
    service = build_service(tmp_path)
    service.upload(filename="quantum.md", content=DOC1.encode())
    service.upload(filename="server.md", content=DOC2.encode())

    matches = service.summarize_document(subject="量子桂花树")
    assert matches
    assert matches[0]["title"] == "quantum.md"
    assert "1785826048" in matches[0]["text"]


def test_summarize_empty_kb_returns_empty(tmp_path):
    service = build_service(tmp_path)
    assert service.summarize_document() == []


def test_summarize_tool(tmp_path):
    service = build_service(tmp_path)
    service.upload(filename="quantum.md", content=DOC1.encode())
    tool = KnowledgeSummarizeTool(summarizer=service.summarize_document)
    result = asyncio.run(tool.run(KnowledgeSummarizeArgs(subject="量子桂花树")))
    assert result.status == "success"
    assert result.data["matches"][0]["title"] == "quantum.md"


def test_intent_routes_summarize():
    router = HeuristicIntentRouter()

    direct = router.route("总结该文档")
    assert direct.type == IntentType.TOOL_CALL
    assert direct.tool_name == "knowledge_summarize"
    assert direct.tool_args["subject"] == ""

    named = router.route("帮我总结一下量子桂花树文档")
    assert named.type == IntentType.TOOL_CALL
    assert named.tool_name == "knowledge_summarize"
    assert named.tool_args["subject"] == "量子桂花树"

    slash = router.route("/summarize 服务器手册")
    assert slash.type == IntentType.TOOL_CALL
    assert slash.tool_name == "knowledge_summarize"
    assert slash.tool_args["subject"] == "服务器手册"

    # 带文件名（用户实际说法）→ 也应触发，且主题保留文件名
    file_named = router.route("总结一下test.md")
    assert file_named.type == IntentType.TOOL_CALL
    assert file_named.tool_name == "knowledge_summarize"
    assert file_named.tool_args["subject"] == "test.md"

    # 总结话题（无文档字样）→ 走普通知识检索，不误入 summarize
    topic = router.route("总结一下量子桂花树的特征")
    assert not (topic.type == IntentType.TOOL_CALL and topic.tool_name == "knowledge_summarize")


# --- 来源去重：同一文档多分片只留一个 citation ---


def test_citations_dedupe_by_document(tmp_path):
    from desktopmate_agent.agent.graph import AgentGraph
    from desktopmate_agent.models.provider import FakeModelProvider
    from desktopmate_agent.tools.registry import ToolRegistry

    graph = AgentGraph(
        model_provider=FakeModelProvider("x"),
        tool_registry=ToolRegistry(),
        intent_router=HeuristicIntentRouter(),
    )
    tool_results = [
        {
            "name": "knowledge_summarize",
            "status": "success",
            "summary": "找到 4 条知识片段",
            "data": {
                "matches": [
                    {"chunk_id": f"c{i}", "text": f"片段{i}", "title": "test.md",
                     "source_path": "/kb/test.md", "document_id": "doc_test",
                     "heading_path": []}
                    for i in range(4)
                ]
            },
        }
    ]
    _, citations = graph._knowledge_from_tool_results(tool_results)
    assert len(citations) == 1  # 4 个分片同一文档 → 1 个来源
    assert citations[0]["document_id"] == "doc_test"
    assert citations[0]["source_path"] == "/kb/test.md"


def test_citations_keep_multiple_documents(tmp_path):
    from desktopmate_agent.agent.graph import AgentGraph
    from desktopmate_agent.models.provider import FakeModelProvider
    from desktopmate_agent.tools.registry import ToolRegistry

    graph = AgentGraph(
        model_provider=FakeModelProvider("x"),
        tool_registry=ToolRegistry(),
        intent_router=HeuristicIntentRouter(),
    )
    tool_results = [
        {
            "name": "knowledge_search",
            "status": "success",
            "summary": "",
            "data": {
                "matches": [
                    {"chunk_id": "a1", "text": "甲", "title": "a.md", "source_path": "/kb/a.md", "document_id": "doc_a"},
                    {"chunk_id": "a2", "text": "乙", "title": "a.md", "source_path": "/kb/a.md", "document_id": "doc_a"},
                    {"chunk_id": "b1", "text": "丙", "title": "b.md", "source_path": "/kb/b.md", "document_id": "doc_b"},
                ]
            },
        }
    ]
    _, citations = graph._knowledge_from_tool_results(tool_results)
    assert len(citations) == 2
    assert {c["source_path"] for c in citations} == {"/kb/a.md", "/kb/b.md"}


# --- 总结回答结构化指令 ---


def test_summarize_structure_instruction_in_prompt(tmp_path):
    from desktopmate_agent.agent.graph import AgentGraph
    from desktopmate_agent.models.provider import FakeModelProvider
    from desktopmate_agent.tools.registry import ToolRegistry

    graph = AgentGraph(
        model_provider=FakeModelProvider("x"),
        tool_registry=ToolRegistry(),
        intent_router=HeuristicIntentRouter(),
    )
    prompt = graph._build_prompt(
        user_text="总结一下test.md",
        memory_context=[],
        knowledge_context=[{"title": "test.md", "content": "内容"}],
        summarize_doc="test.md",
    )
    assert "回答格式规范" in prompt  # 场景化格式规范常驻系统提示
    assert "文档总结" in prompt
    assert "我看了下「test.md」" in prompt  # 总结开场示例
    assert "分条列出要点" in prompt
