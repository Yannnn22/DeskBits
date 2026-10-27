import asyncio
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from desktopmate_agent.knowledge.cleanup import cleanup_stale
from desktopmate_agent.knowledge.service import DocumentService
from desktopmate_agent.knowledge.store import DocumentStore
from desktopmate_agent.rag.embeddings import FakeEmbeddingProvider
from desktopmate_agent.rag.strategies import RAGScenario, RAGStrategy
from desktopmate_agent.tools.knowledge_tools import KnowledgeSearchArgs, KnowledgeSearchTool


def build_search_tool(tmp_path: Path) -> tuple[DocumentStore, KnowledgeSearchTool]:
    store = DocumentStore(tmp_path / "desktopmate.db")
    tool = KnowledgeSearchTool(
        knowledge_root=tmp_path / "knowledge",
        embedding_provider=FakeEmbeddingProvider(dimensions=16),
        embedding_dimensions=16,
        document_store=store,
        embedding_id="fake:test",
        strategy=RAGStrategy(
            scenario=RAGScenario.PERSONAL_ASSISTANT,
            chunk_size=500,
            overlap=80,
            keyword_weight=0.0,
            vector_weight=1.0,
            rerank_top_n=20,
            minimum_score=0.0,
            notes="test",
        ),
        reranker=None,
    )
    return store, tool


def build_document_service(tmp_path: Path) -> DocumentService:
    store, tool = build_search_tool(tmp_path)
    provider = FakeEmbeddingProvider(dimensions=16)
    return DocumentService(
        store=store,
        originals_dir=tmp_path / "knowledge" / "originals",
        extracted_dir=tmp_path / "knowledge" / "extracted",
        search_tool=tool,
        embedding_provider=provider,
        embedding_id="fake:test",
    )


def add_indexed_document(
    store: DocumentStore,
    *,
    name: str,
    text: str,
    importance: float,
) -> str:
    document = store.add_document(
        name=name,
        file_type="MD",
        size=len(text.encode("utf-8")),
        importance=importance,
    )
    store.replace_chunks(
        document.id,
        [
            {
                "id": f"{document.id}_chunk",
                "chunk_index": 0,
                "text": text,
                "token_count": max(1, len(text) // 4),
            }
        ],
    )
    store.set_document_status(document.id, status="indexed", chunk_count=1)
    return document.id


def test_upload_passes_importance_to_store(tmp_path):
    service = build_document_service(tmp_path)

    document = service.upload(
        filename="important.md",
        content=b"# Important\nrank me",
        importance=0.9,
    )

    assert document.importance == 0.9
    assert service.store.get_document(document.id).importance == 0.9


def test_search_ranks_by_similarity_times_importance(tmp_path):
    store, tool = build_search_tool(tmp_path)
    add_indexed_document(store, name="low.md", text="alpha beta", importance=0.2)
    add_indexed_document(store, name="high.md", text="alpha beta", importance=0.9)
    tool.refresh_index()

    result = asyncio.run(tool.run(KnowledgeSearchArgs(query="alpha beta", limit=2)))

    assert [match["title"] for match in result.data["matches"]] == ["high.md", "low.md"]


def test_importance_can_promote_slightly_weaker_match(tmp_path):
    store, tool = build_search_tool(tmp_path)
    add_indexed_document(store, name="specific.md", text="alpha beta gamma", importance=0.2)
    add_indexed_document(store, name="preferred.md", text="alpha beta", importance=0.9)
    tool.refresh_index()

    result = asyncio.run(tool.run(KnowledgeSearchArgs(query="alpha beta gamma", limit=2)))

    assert result.data["matches"][0]["title"] == "preferred.md"


def test_cleanup_marks_low_importance_stale_documents_inactive(tmp_path):
    store = DocumentStore(tmp_path / "desktopmate.db")
    doc_id = add_indexed_document(store, name="stale.md", text="old preference", importance=0.2)
    old = datetime.now(UTC) - timedelta(days=31)

    with sqlite3.connect(store.db_path) as connection:
        connection.execute(
            "UPDATE documents SET last_accessed = ? WHERE id = ?",
            (old.isoformat(), doc_id),
        )

    assert cleanup_stale(store, now=datetime.now(UTC)) == 1
    assert store.get_document(doc_id).active is False


def test_search_does_not_return_inactive_documents(tmp_path):
    store, tool = build_search_tool(tmp_path)
    doc_id = add_indexed_document(store, name="stale.md", text="old preference", importance=0.2)
    old = datetime.now(UTC) - timedelta(days=31)
    with sqlite3.connect(store.db_path) as connection:
        connection.execute(
            "UPDATE documents SET last_accessed = ? WHERE id = ?",
            (old.isoformat(), doc_id),
        )
    cleanup_stale(store, now=datetime.now(UTC))
    tool.refresh_index()

    result = asyncio.run(tool.run(KnowledgeSearchArgs(query="old preference", limit=3)))

    assert result.data["matches"] == []
