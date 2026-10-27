import asyncio
import threading
from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel, Field

from desktopmate_agent.knowledge.store import DocumentStore
from desktopmate_agent.rag.chunking import MarkdownChunker
from desktopmate_agent.rag.embeddings import EmbeddingProvider, FakeEmbeddingProvider
from desktopmate_agent.rag.evaluation import RetrievalEvalCase, run_retrieval_evaluation
from desktopmate_agent.rag.hybrid import HybridRetriever
from desktopmate_agent.rag.ingestion import LocalDocumentIngestionService
from desktopmate_agent.rag.loaders import TextDocumentLoader
from desktopmate_agent.rag.milvus import MilvusCollectionConfig, MilvusVectorStore
from desktopmate_agent.rag.models import KnowledgeChunk
from desktopmate_agent.rag.rerank import Reranker, RuleBasedReranker
from desktopmate_agent.rag.strategies import RAGScenario, RAGStrategy, get_rag_strategy
from desktopmate_agent.rag.vector_store import InMemoryVectorStore, RetrievalResult
from desktopmate_agent.tools.base import BaseTool, RiskLevel, ToolResult


class KnowledgeSearchArgs(BaseModel):
    query: str = Field(min_length=1)
    limit: int = Field(default=5, ge=1, le=20)


class KnowledgeRefreshArgs(BaseModel):
    pass


class KnowledgeEvaluateArgs(BaseModel):
    cases: list[RetrievalEvalCase] = Field(min_length=1)
    k: int = Field(default=5, ge=1, le=20)


class KnowledgeSearchTool(BaseTool[KnowledgeSearchArgs]):
    name = "knowledge_search"
    description = "Search the local personal knowledge base with hybrid retrieval."
    args_schema = KnowledgeSearchArgs
    risk_level = RiskLevel.READ
    requires_permission = False

    def __init__(
        self,
        *,
        knowledge_root: Path,
        milvus_enabled: bool = False,
        milvus_collection_name: str = "desktopmate_knowledge",
        milvus_uri: str = "http://127.0.0.1:19530",
        embedding_dimensions: int = 128,
        embedding_provider: EmbeddingProvider | None = None,
        milvus_client: object | None = None,
        document_store: DocumentStore | None = None,
        embedding_id: str | None = None,
        strategy: RAGStrategy | None = None,
        reranker: Reranker | None = None,
    ) -> None:
        self.knowledge_root = knowledge_root.resolve()
        self.milvus_enabled = milvus_enabled
        self.milvus_collection_name = milvus_collection_name
        self.milvus_uri = milvus_uri
        self.embedding_dimensions = embedding_dimensions
        self.embedding_provider = embedding_provider or FakeEmbeddingProvider(dimensions=embedding_dimensions)
        self.milvus_client = milvus_client
        # S7: 统一摄入源——优先 SQLite document_chunks（上传/重建入口），
        # 无 document_store 时回退到目录摄入（保持旧行为/测试兼容）。
        self.document_store = document_store
        self.embedding_id = embedding_id
        self.strategy = strategy
        self.reranker = reranker
        self._index_lock = threading.Lock()
        self.retriever = self._build_retriever()
        self.chunk_count = self.refresh_index()

    def refresh_index(self) -> int:
        with self._index_lock:
            retriever = self._build_retriever()
            if self.document_store is not None and self.embedding_id is not None:
                chunk_count = self._ingest_from_db(retriever)
            else:
                chunk_count = self._ingest_from_directory(retriever)
            self.retriever = retriever
            self.chunk_count = chunk_count
            return chunk_count

    def _ingest_from_directory(self, retriever: HybridRetriever) -> int:
        if not self.knowledge_root.exists():
            return 0
        chunks = LocalDocumentIngestionService(
            loader=TextDocumentLoader(),
            chunker=MarkdownChunker(chunk_size=500, overlap=80),
        ).ingest_directory(self.knowledge_root)
        retriever.add_chunks(chunks)
        return len(chunks)

    def _ingest_from_db(self, retriever: HybridRetriever) -> int:
        loaded = self.document_store.load_chunks_with_embeddings(self.embedding_id)
        if not loaded:
            return 0
        chunks: list[KnowledgeChunk] = []
        embeddings: list[list[float]] = []
        missing_rows: list[tuple[int, str]] = []
        for index, (row, vector) in enumerate(loaded):
            metadata = dict(row["metadata"])
            chunks.append(
                KnowledgeChunk(
                    chunk_id=str(row["id"]),
                    document_id=str(row["document_id"]),
                    text=str(row["text"]),
                    chunk_index=int(row["chunk_index"]),
                    start_char=int(metadata.get("start_char", 0)),
                    end_char=int(metadata.get("end_char", 0)),
                    metadata=metadata,
                )
            )
            if vector is None:
                missing_rows.append((index, str(row["text"])))
            embeddings.append(vector)
        if missing_rows:
            generated = self.embedding_provider.embed_documents(
                [text for _, text in missing_rows]
            )
            for (index, _), vector in zip(missing_rows, generated, strict=True):
                embeddings[index] = vector
                if self.document_store is not None and self.embedding_id is not None:
                    self.document_store.save_chunk_embedding(
                        str(loaded[index][0]["id"]),
                        self.embedding_id,
                        vector,
                    )
        retriever.add_chunks(chunks, embeddings=embeddings)
        return len(chunks)

    def _build_retriever(self) -> HybridRetriever:
        vector_store: InMemoryVectorStore | MilvusVectorStore
        if self.milvus_enabled:
            vector_store = MilvusVectorStore(
                config=MilvusCollectionConfig(
                    collection_name=self.milvus_collection_name,
                    embedding_dimensions=self.embedding_dimensions,
                    uri=self.milvus_uri,
                ),
                embedding_provider=self.embedding_provider,
                client=self.milvus_client,
            )
        else:
            vector_store = InMemoryVectorStore(embedding_provider=self.embedding_provider)
        return HybridRetriever(
            embedding_provider=self.embedding_provider,
            strategy=self.strategy or get_rag_strategy(RAGScenario.PERSONAL_ASSISTANT),
            reranker=self.reranker or RuleBasedReranker(),
            vector_store=vector_store,
        )

    async def run(self, args: KnowledgeSearchArgs) -> ToolResult:
        results = await asyncio.to_thread(self._search, args.query, args.limit)
        return ToolResult(
            status="success",
            summary=f"找到 {len(results)} 条知识片段",
            data={"matches": [_serialize_result(result) for result in results]},
        )

    def _search(self, query: str, limit: int) -> list[RetrievalResult]:
        with self._index_lock:
            mentioned_document_ids = self._mentioned_document_ids(query)
            if not mentioned_document_ids:
                results = self.retriever.search(query, limit=max(limit, self.chunk_count))
                ranked = self._rank_by_importance(results, limit=limit)
                self._touch_results(ranked)
                return ranked
            candidates = self.retriever.search(query, limit=max(limit, self.chunk_count))
            scoped = [
                result
                for result in candidates
                if result.chunk.document_id in mentioned_document_ids
            ]
            ranked = self._rank_by_importance(scoped, limit=limit)
            self._touch_results(ranked)
            return ranked

    def _rank_by_importance(
        self,
        results: list[RetrievalResult],
        *,
        limit: int,
    ) -> list[RetrievalResult]:
        weighted = [
            RetrievalResult(
                chunk=result.chunk,
                score=result.score * float(result.chunk.metadata.get("importance", 0.5)),
            )
            for result in results
        ]
        return sorted(weighted, key=lambda result: (-result.score, result.chunk.chunk_id))[:limit]

    def _touch_results(self, results: list[RetrievalResult]) -> None:
        if self.document_store is None:
            return
        self.document_store.touch_documents({result.chunk.document_id for result in results})

    def _mentioned_document_ids(self, query: str) -> set[str]:
        if self.document_store is None:
            return set()
        normalized_query = query.casefold()
        matches: set[str] = set()
        for document in self.document_store.list_documents(status="indexed"):
            name = document.name.casefold()
            stem = Path(document.name).stem.casefold()
            if name in normalized_query or (stem and stem in normalized_query):
                matches.add(document.id)
        return matches


class KnowledgeSaveArgs(BaseModel):
    title: str = Field(default="", max_length=200)
    content: str = Field(min_length=1, max_length=20_000)


class KnowledgeSaveTool(BaseTool[KnowledgeSaveArgs]):
    name = "knowledge_save"
    description = "把一条信息保存到个人知识库（标题 + 内容），之后的提问会自动检索到它。"
    args_schema = KnowledgeSaveArgs
    risk_level = RiskLevel.WRITE
    requires_permission = False

    def __init__(self, *, saver: Callable[[str, str], str]) -> None:
        self.saver = saver

    async def run(self, args: KnowledgeSaveArgs) -> ToolResult:
        try:
            document_id = self.saver(args.title, args.content)
        except Exception as exc:  # noqa: BLE001
            return ToolResult(
                status="error",
                summary=f"保存到知识库失败：{exc}",
                data={"error": str(exc)},
            )
        display = args.title or (args.content[:24] + ("…" if len(args.content) > 24 else ""))
        return ToolResult(
            status="success",
            summary=f"已保存到知识库：{display}",
            data={"document_id": document_id, "title": display},
        )


class KnowledgeSummarizeArgs(BaseModel):
    subject: str = Field(default="", max_length=200)


class KnowledgeSummarizeTool(BaseTool[KnowledgeSummarizeArgs]):
    name = "knowledge_summarize"
    description = "Summarize an uploaded document in the personal knowledge base. Subject optional; defaults to the most recently uploaded document."
    args_schema = KnowledgeSummarizeArgs
    risk_level = RiskLevel.READ
    requires_permission = False

    def __init__(self, *, summarizer: Callable[[str], list[dict[str, object]]]) -> None:
        self.summarizer = summarizer

    async def run(self, args: KnowledgeSummarizeArgs) -> ToolResult:
        matches = self.summarizer(args.subject)
        return ToolResult(
            status="success",
            summary=f"找到 {len(matches)} 条知识片段",
            data={"matches": matches},
        )


class KnowledgeRefreshTool(BaseTool[KnowledgeRefreshArgs]):
    name = "knowledge_refresh"
    description = "Refresh the local personal knowledge base index."
    args_schema = KnowledgeRefreshArgs
    risk_level = RiskLevel.READ
    requires_permission = False

    def __init__(self, *, search_tool: KnowledgeSearchTool) -> None:
        self.search_tool = search_tool

    async def run(self, args: KnowledgeRefreshArgs) -> ToolResult:
        chunk_count = self.search_tool.refresh_index()
        return ToolResult(
            status="success",
            summary=f"知识库索引已刷新，当前 {chunk_count} 个片段",
            data={"chunk_count": chunk_count},
        )


class KnowledgeEvaluateTool(BaseTool[KnowledgeEvaluateArgs]):
    name = "knowledge_evaluate"
    description = "Evaluate the current local knowledge retriever with labeled retrieval cases."
    args_schema = KnowledgeEvaluateArgs
    risk_level = RiskLevel.READ
    requires_permission = False

    def __init__(self, *, search_tool: KnowledgeSearchTool) -> None:
        self.search_tool = search_tool

    async def run(self, args: KnowledgeEvaluateArgs) -> ToolResult:
        evaluation_run = run_retrieval_evaluation(self.search_tool.retriever, args.cases, k=args.k)
        report = evaluation_run.report
        return ToolResult(
            status="success",
            summary=f"RAG 评估完成：HitRate@{report.k}={report.hit_rate:.2f}, MRR@{report.k}={report.mrr:.2f}",
            data={
                "report": report.model_dump(),
                "cases": [case.model_dump() for case in evaluation_run.cases],
            },
        )


def _serialize_result(result: RetrievalResult) -> dict[str, object]:
    return {
        "chunk_id": result.chunk.chunk_id,
        "document_id": result.chunk.document_id,
        "score": result.score,
        "text": result.chunk.text,
        "source_path": result.chunk.metadata.get("source_path"),
        "title": result.chunk.metadata.get("title"),
        "heading_path": result.chunk.metadata.get("heading_path", []),
    }
