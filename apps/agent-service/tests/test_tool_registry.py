from pathlib import Path

import pytest
from pydantic import BaseModel

from desktopmate_agent.tools.base import BaseTool, RiskLevel, ToolResult
from desktopmate_agent.tools.file_tools import DraftWriteTool, ReadFileTool, SearchFilesTool
from desktopmate_agent.tools.knowledge_tools import KnowledgeEvaluateTool, KnowledgeRefreshTool, KnowledgeSearchTool
from desktopmate_agent.tools.registry import ToolRegistry


class EchoArgs(BaseModel):
    text: str


class EchoTool(BaseTool[EchoArgs]):
    name = "echo"
    description = "Echo input text"
    args_schema = EchoArgs
    risk_level = RiskLevel.READ
    requires_permission = False

    async def run(self, args: EchoArgs) -> ToolResult:
        return ToolResult(status="success", summary=args.text, data={"text": args.text})


class FakeMilvusClient:
    def __init__(self) -> None:
        self.collections: set[str] = set()
        self.search_rows: list[list[dict[str, object]]] = []

    def has_collection(self, collection_name: str) -> bool:
        return collection_name in self.collections

    def prepare_index_params(self):
        return FakeIndexParams()

    def create_collection(self, **kwargs):
        self.collections.add(kwargs["collection_name"])

    def load_collection(self, collection_name: str, **kwargs):
        self.collections.add(collection_name)

    def insert(self, collection_name: str, data, **kwargs):
        self.collections.add(collection_name)
        return {"insert_count": len(data)}

    def search(self, collection_name: str, data=None, limit: int = 10, output_fields=None, **kwargs):
        return self.search_rows


class FakeIndexParams:
    def __init__(self) -> None:
        self.field_name = "embedding"

    def add_index(self, **kwargs):
        return None


def test_registry_registers_and_runs_tool():
    registry = ToolRegistry()
    registry.register(EchoTool())

    tool = registry.get("echo")

    assert tool.name == "echo"
    assert registry.has("echo") is True
    assert registry.has("missing") is False


def test_registry_rejects_duplicate_tool_name():
    registry = ToolRegistry()
    registry.register(EchoTool())

    with pytest.raises(ValueError, match="already registered"):
        registry.register(EchoTool())


@pytest.mark.asyncio
async def test_search_files_tool_limits_results_to_allowed_root(tmp_allowed_root):
    note = tmp_allowed_root / "note.md"
    note.write_text("# DesktopMate\n", encoding="utf-8")
    outside = tmp_allowed_root.parent / "note-outside.md"
    outside.write_text("secret", encoding="utf-8")

    tool = SearchFilesTool(allowed_roots=[tmp_allowed_root])
    args = tool.parse_args({"query": "note", "limit": 5})

    result = await tool.run(args)

    assert result.status == "success"
    assert result.data["matches"] == [str(note)]


@pytest.mark.asyncio
async def test_search_files_tool_ignores_symlink_escape(tmp_allowed_root):
    outside = tmp_allowed_root.parent / "note-secret.md"
    outside.write_text("secret", encoding="utf-8")
    symlink = tmp_allowed_root / "note-link.md"
    symlink.symlink_to(outside)

    tool = SearchFilesTool(allowed_roots=[tmp_allowed_root])
    args = tool.parse_args({"query": "note", "limit": 5})

    result = await tool.run(args)

    assert result.status == "success"
    assert result.data["matches"] == []


@pytest.mark.asyncio
async def test_read_file_tool_reads_text_file_inside_allowed_root(tmp_allowed_root):
    note = tmp_allowed_root / "note.md"
    note.write_text("# DesktopMate\n", encoding="utf-8")

    tool = ReadFileTool(allowed_roots=[tmp_allowed_root])
    args = tool.parse_args({"path": str(note)})

    result = await tool.run(args)

    assert result.status == "success"
    assert result.summary == "读取文件成功"
    assert result.data["path"] == str(note.resolve())
    assert result.data["content"] == "# DesktopMate\n"


@pytest.mark.asyncio
async def test_read_file_tool_rejects_file_outside_allowed_root(tmp_allowed_root):
    outside = tmp_allowed_root.parent / "secret.md"
    outside.write_text("secret", encoding="utf-8")

    tool = ReadFileTool(allowed_roots=[tmp_allowed_root])
    args = tool.parse_args({"path": str(outside)})

    result = await tool.run(args)

    assert result.status == "error"
    assert result.summary == "文件不在允许访问的目录内"


@pytest.mark.asyncio
async def test_read_file_tool_rejects_symlink_escape(tmp_allowed_root):
    outside = tmp_allowed_root.parent / "secret.md"
    outside.write_text("secret", encoding="utf-8")
    symlink = tmp_allowed_root / "secret-link.md"
    symlink.symlink_to(outside)

    tool = ReadFileTool(allowed_roots=[tmp_allowed_root])
    args = tool.parse_args({"path": str(symlink)})

    result = await tool.run(args)

    assert result.status == "error"
    assert result.summary == "文件不在允许访问的目录内"


@pytest.mark.asyncio
async def test_draft_write_tool_creates_draft_without_touching_target_file(
    tmp_allowed_root, tmp_path
):
    target = tmp_allowed_root / "note.md"
    draft_root = tmp_path / "drafts"

    tool = DraftWriteTool(allowed_roots=[tmp_allowed_root], draft_root=draft_root)
    args = tool.parse_args({"path": str(target), "content": "# Draft\n"})

    result = await tool.run(args)

    assert result.status == "success"
    assert result.summary == "已生成写入草稿"
    assert not target.exists()
    draft_path = result.data["draft_path"]
    assert draft_path.startswith(str(draft_root))
    assert "target_path:" in Path(draft_path).read_text(encoding="utf-8")
    assert "# Draft\n" in Path(draft_path).read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_draft_write_tool_rejects_target_outside_allowed_root(tmp_allowed_root, tmp_path):
    target = tmp_allowed_root.parent / "secret.md"
    tool = DraftWriteTool(allowed_roots=[tmp_allowed_root], draft_root=tmp_path / "drafts")
    args = tool.parse_args({"path": str(target), "content": "secret"})

    result = await tool.run(args)

    assert result.status == "error"
    assert result.summary == "目标文件不在允许访问的目录内"


@pytest.mark.asyncio
async def test_knowledge_search_tool_returns_ranked_chunks(tmp_path):
    knowledge_root = tmp_path / "knowledge"
    knowledge_root.mkdir()
    note = knowledge_root / "milvus.md"
    note.write_text("# Milvus 配置\n\n先启动 Docker，再创建 collection。", encoding="utf-8")
    tool = KnowledgeSearchTool(knowledge_root=knowledge_root)
    args = tool.parse_args({"query": "怎么配置 Milvus", "limit": 1})

    result = await tool.run(args)

    assert result.status == "success"
    assert result.summary == "找到 1 条知识片段"
    assert result.data["matches"][0]["chunk_id"].endswith(":0")
    assert result.data["matches"][0]["document_id"]
    assert result.data["matches"][0]["text"] == "先启动 Docker，再创建 collection。"
    assert result.data["matches"][0]["source_path"] == str(note)
    assert result.data["matches"][0]["heading_path"] == ["Milvus 配置"]


@pytest.mark.asyncio
async def test_knowledge_search_tool_can_use_milvus_backend(tmp_path):
    knowledge_root = tmp_path / "knowledge"
    knowledge_root.mkdir()
    note = knowledge_root / "milvus.md"
    note.write_text("# Milvus 配置\n\n先启动 Docker，再创建 collection。", encoding="utf-8")
    tool = KnowledgeSearchTool(
        knowledge_root=knowledge_root,
        milvus_enabled=True,
        milvus_collection_name="desktopmate_knowledge",
        embedding_dimensions=16,
        milvus_client=FakeMilvusClient(),
    )
    args = tool.parse_args({"query": "怎么配置 Milvus", "limit": 1})

    result = await tool.run(args)

    assert result.status == "success"
    assert result.data["matches"][0]["source_path"] == str(note)
    assert result.data["matches"][0]["heading_path"] == ["Milvus 配置"]


@pytest.mark.asyncio
async def test_knowledge_search_tool_can_use_injected_embedding_provider(tmp_path):
    class RecordingEmbeddingProvider:
        def __init__(self) -> None:
            self.document_batches = []
            self.queries = []

        def embed_documents(self, texts: list[str]) -> list[list[float]]:
            self.document_batches.append(texts)
            return [[1.0, 0.0] for _ in texts]

        def embed_query(self, text: str) -> list[float]:
            self.queries.append(text)
            return [1.0, 0.0]

    knowledge_root = tmp_path / "knowledge"
    knowledge_root.mkdir()
    (knowledge_root / "rag.md").write_text("# RAG\n\n混合检索需要向量召回。", encoding="utf-8")
    embedding_provider = RecordingEmbeddingProvider()
    tool = KnowledgeSearchTool(knowledge_root=knowledge_root, embedding_provider=embedding_provider)
    args = tool.parse_args({"query": "向量召回", "limit": 1})

    result = await tool.run(args)

    assert result.status == "success"
    assert embedding_provider.document_batches == [["混合检索需要向量召回。"]]
    assert embedding_provider.queries == ["向量召回"]


@pytest.mark.asyncio
async def test_knowledge_refresh_tool_rebuilds_search_index(tmp_path):
    knowledge_root = tmp_path / "knowledge"
    knowledge_root.mkdir()
    search_tool = KnowledgeSearchTool(knowledge_root=knowledge_root)
    refresh_tool = KnowledgeRefreshTool(search_tool=search_tool)
    (knowledge_root / "new.md").write_text("# 新知识\n\nBM25 可以提升关键词召回。", encoding="utf-8")

    before = await search_tool.run(search_tool.parse_args({"query": "BM25", "limit": 1}))
    refreshed = await refresh_tool.run(refresh_tool.parse_args({}))
    after = await search_tool.run(search_tool.parse_args({"query": "BM25", "limit": 1}))

    assert before.summary == "找到 0 条知识片段"
    assert refreshed.status == "success"
    assert refreshed.summary == "知识库索引已刷新，当前 1 个片段"
    assert after.summary == "找到 1 条知识片段"
    assert after.data["matches"][0]["source_path"] == str(knowledge_root / "new.md")


@pytest.mark.asyncio
async def test_knowledge_evaluate_tool_scores_cases_against_current_retriever(tmp_path):
    knowledge_root = tmp_path / "knowledge"
    knowledge_root.mkdir()
    (knowledge_root / "milvus.md").write_text(
        "# Milvus\n\nMilvus 需要先启动 Docker 服务并配置 collection。",
        encoding="utf-8",
    )
    search_tool = KnowledgeSearchTool(knowledge_root=knowledge_root)
    relevant_chunk_id = search_tool.retriever.search("怎么配置 Milvus", limit=1)[0].chunk.chunk_id
    evaluate_tool = KnowledgeEvaluateTool(search_tool=search_tool)
    args = evaluate_tool.parse_args(
        {
            "cases": [
                {"query": "怎么配置 Milvus", "relevant_chunk_ids": [relevant_chunk_id]},
            ],
            "k": 1,
        }
    )

    result = await evaluate_tool.run(args)

    assert result.status == "success"
    assert result.summary == "RAG 评估完成：HitRate@1=1.00, MRR@1=1.00"
    assert result.data["report"] == {
        "k": 1,
        "case_count": 1,
        "hit_rate": 1.0,
        "mrr": 1.0,
    }


# --- H3: safe_get 与禁用工具审计不崩溃 ---


def test_safe_get_returns_none_for_missing_or_disabled():
    registry = ToolRegistry()
    registry.register(EchoTool())

    assert registry.safe_get("echo") is not None
    assert registry.safe_get("missing") is None

    registry.set_enabled("echo", False)
    assert registry.safe_get("echo") is None
    assert registry.has("echo") is False


def test_audit_tool_calls_skips_disabled_tool_without_crashing(tmp_path):
    """工具在执行中途被禁用时，_audit_tool_calls 不抛 KeyError。"""

    from desktopmate_agent.agent.service import AgentService
    from desktopmate_agent.trace.store import JsonlTraceStore

    registry = ToolRegistry()
    registry.register(EchoTool())
    service = AgentService(
        model_provider=object(),  # type: ignore[arg-type]
        tool_registry=registry,
        trace_store=JsonlTraceStore(tmp_path),
    )
    # 先禁用再审计：audit 应标记 unknown 而非崩溃
    registry.set_enabled("echo", False)
    audited = service._audit_tool_calls(
        [{"name": "echo", "args": {"text": "hi"}}],
        run_id="r1",
    )
    assert audited[0]["risk_level"] == "unknown"
    assert audited[0]["requires_permission"] is False
