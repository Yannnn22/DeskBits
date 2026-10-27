"""S7 RAG 生产化：embedding 落库、DB 统一摄入、index/status、异步重建 job。"""

import time
from pathlib import Path

from fastapi.testclient import TestClient

from desktopmate_agent.config import Settings
from desktopmate_agent.knowledge.service import DocumentService
from desktopmate_agent.knowledge.store import DocumentStore
from desktopmate_agent.main import create_app
from desktopmate_agent.rag.embeddings import FakeEmbeddingProvider
from desktopmate_agent.rag.rerank import RuleBasedReranker
from desktopmate_agent.tools.knowledge_tools import KnowledgeSearchTool

AUTH_HEADERS = {"X-DesktopMate-Token": "test-token"}
EMBEDDING_ID = "fake:text-embedding-3-small"
DOC_TEXT = "# 测试文档\n量子桂花树是验证 RAG 闭环的独特名词，批次 1785826048。\n"


def make_settings(tmp_path: Path, **overrides) -> Settings:
    defaults = {
        "trace_dir": tmp_path / "traces",
        "settings_dir": tmp_path / "settings",
        "app_data_dir": tmp_path,
        "knowledge_dir": tmp_path / "knowledge",
        "knowledge_db_path": tmp_path / "desktopmate.db",
        "auth_token": "test-token",
        "openai_api_key": None,
        "deepseek_api_key": None,
        "embedding_provider": "fake",
        "embedding_dimensions": 16,
    }
    defaults.update(overrides)
    return Settings(**defaults)


def build_document_service(tmp_path: Path) -> DocumentService:
    settings = make_settings(tmp_path)
    provider = FakeEmbeddingProvider(dimensions=16)
    store = DocumentStore(settings.knowledge_db_path)
    tool = KnowledgeSearchTool(
        knowledge_root=settings.knowledge_dir,
        embedding_provider=provider,
        embedding_dimensions=16,
        document_store=store,
        embedding_id=EMBEDDING_ID,
    )
    return DocumentService(
        store=store,
        originals_dir=settings.knowledge_dir / "originals",
        extracted_dir=settings.knowledge_dir / "extracted",
        search_tool=tool,
        embedding_provider=provider,
        embedding_id=EMBEDDING_ID,
    )


def upload_doc(service: DocumentService, name: str = "test.md", text: str = DOC_TEXT) -> None:
    service.upload(filename=name, content=text.encode("utf-8"))


# --- embedding 落库 ---


def test_upload_persists_embeddings(tmp_path):
    service = build_document_service(tmp_path)
    upload_doc(service)

    docs = service.store.list_documents()
    assert len(docs) == 1
    chunks = service.store.list_chunks(docs[0].id)
    assert len(chunks) >= 1
    assert all(chunk["embedding_id"] == EMBEDDING_ID for chunk in chunks)
    # chunk_embeddings 表有对应向量
    loaded = service.store.load_chunks_with_embeddings(EMBEDDING_ID)
    assert len(loaded) == len(chunks)
    assert all(vector is not None and len(vector) == 16 for _, vector in loaded)


def test_upload_without_provider_skips_embeddings(tmp_path):
    settings = make_settings(tmp_path)
    store = DocumentStore(settings.knowledge_db_path)
    tool = KnowledgeSearchTool(
        knowledge_root=settings.knowledge_dir,
        document_store=store,
        embedding_id=EMBEDDING_ID,
    )
    service = DocumentService(
        store=store,
        originals_dir=settings.knowledge_dir / "originals",
        extracted_dir=settings.knowledge_dir / "extracted",
        search_tool=tool,
    )
    upload_doc(service)
    docs = service.store.list_documents()
    chunks = service.store.list_chunks(docs[0].id)
    assert all(chunk["embedding_id"] is None for chunk in chunks)


# --- 工具 DB 摄入 + 检索闭环 ---


def test_tool_ingests_from_db_and_searches(tmp_path):
    service = build_document_service(tmp_path)
    upload_doc(service)
    # 上传后工具自动 refresh，从 DB 摄入
    tool = service.search_tool
    assert tool.chunk_count >= 1

    import asyncio

    from desktopmate_agent.tools.knowledge_tools import KnowledgeSearchArgs

    result = asyncio.run(tool.run(KnowledgeSearchArgs(query="量子桂花树", limit=5)))
    assert result.status == "success"
    matches = result.data["matches"]
    assert len(matches) >= 1
    assert "量子桂花树" in matches[0]["text"]
    assert matches[0]["title"] == "test.md"


def test_embedding_version_change_rebuilds_stale_chunks_instead_of_hiding_them(tmp_path):
    service = build_document_service(tmp_path)
    upload_doc(service)
    new_embedding_id = "fake:replacement-model"
    replacement = KnowledgeSearchTool(
        knowledge_root=service.search_tool.knowledge_root,
        embedding_provider=FakeEmbeddingProvider(dimensions=16),
        embedding_dimensions=16,
        document_store=service.store,
        embedding_id=new_embedding_id,
    )

    import asyncio

    from desktopmate_agent.tools.knowledge_tools import KnowledgeSearchArgs

    result = asyncio.run(replacement.run(KnowledgeSearchArgs(query="量子桂花树", limit=5)))
    assert result.data["matches"]
    assert all(vector is not None for _, vector in service.store.load_chunks_with_embeddings(new_embedding_id))


def test_tool_reranker_wired_by_default(tmp_path):
    settings = make_settings(tmp_path)
    tool = KnowledgeSearchTool(knowledge_root=settings.knowledge_dir, embedding_dimensions=16)
    assert isinstance(tool.retriever.reranker, RuleBasedReranker)


# --- index/status + 异步 jobs ---


def test_index_status_endpoint(tmp_path):
    settings = make_settings(tmp_path)
    client = TestClient(create_app(settings=settings))
    assert client.get("/api/knowledge/index/status").status_code == 401

    resp = client.get("/api/knowledge/index/status", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    for field in ("documents", "chunks", "embedding_provider", "vector_store", "chunk_strategy", "last_refresh_at"):
        assert field in body
    assert body["documents"] == 0
    assert body["chunks"] == 0


def test_index_job_lifecycle(tmp_path):
    settings = make_settings(tmp_path)
    client = TestClient(create_app(settings=settings))

    # 先上传一份文档，让 job 有事可做（回填 embedding + 重建）
    with client:
        uploaded = client.post(
            "/api/knowledge/documents",
            headers=AUTH_HEADERS,
            files={"file": ("test.md", DOC_TEXT.encode(), "text/markdown")},
        )
    assert uploaded.status_code == 200

    created = client.post("/api/knowledge/index/jobs", headers=AUTH_HEADERS)
    assert created.status_code == 200
    job_id = created.json()["job_id"]
    assert created.json()["status"] == "queued"

    body = {}
    for _ in range(200):
        body = client.get(f"/api/knowledge/index/jobs/{job_id}", headers=AUTH_HEADERS).json()
        if body["status"] in ("completed", "failed"):
            break
        time.sleep(0.01)
    assert body["status"] == "completed", body
    assert body["chunk_count"] >= 1

    # 元数据已写入
    status = client.get("/api/knowledge/index/status", headers=AUTH_HEADERS).json()
    assert status["documents"] == 1
    assert status["chunks"] >= 1
    assert status["embedding_provider"] == "FakeEmbeddingProvider"
    assert status["last_refresh_at"] is not None

    assert client.get("/api/knowledge/index/jobs/nope", headers=AUTH_HEADERS).status_code == 404
