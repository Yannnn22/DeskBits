"""知识库文档上传与索引管线。

状态机: queued -> parsing -> indexed / failed。
上传流程：保存原文件(originals) -> 解析文本 -> 切 chunk（落库 document_chunks）
-> 写 extracted 文本 -> 触发 KnowledgeSearchTool 全量重建索引。
"""

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path

from desktopmate_agent.knowledge.store import (
    SUPPORTED_EXTENSIONS,
    DocumentStore,
    KnowledgeDocumentRecord,
)
from desktopmate_agent.rag.chunking import MarkdownChunker
from desktopmate_agent.rag.embeddings import EmbeddingProvider
from desktopmate_agent.rag.loaders import (
    DocxDocumentLoader,
    PdfDocumentLoader,
    TextDocumentLoader,
)
from desktopmate_agent.tools.knowledge_tools import KnowledgeSearchTool

_TEXT_LOADER = TextDocumentLoader()
_PDF_LOADER = PdfDocumentLoader()
_DOCX_LOADER = DocxDocumentLoader()

_MIME_TYPES = {
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".txt": "text/plain",
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


class DocumentUploadError(ValueError):
    pass


class DocumentService:
    def __init__(
        self,
        *,
        store: DocumentStore,
        originals_dir: Path,
        extracted_dir: Path,
        search_tool: KnowledgeSearchTool,
        max_upload_bytes: int = 10 * 1024 * 1024,
        embedding_provider: EmbeddingProvider | None = None,
        embedding_id: str | None = None,
        chunk_strategy: str = "markdown/500/80",
    ) -> None:
        self.store = store
        self.originals_dir = originals_dir
        self.extracted_dir = extracted_dir
        self.originals_dir.mkdir(parents=True, exist_ok=True)
        self.extracted_dir.mkdir(parents=True, exist_ok=True)
        self.search_tool = search_tool
        self.max_upload_bytes = max_upload_bytes
        self.embedding_provider = embedding_provider
        self.embedding_id = embedding_id
        self.chunk_strategy = chunk_strategy

    def upload(
        self,
        *,
        filename: str,
        content: bytes,
        collection_id: str | None = None,
        importance: float = 0.5,
    ) -> KnowledgeDocumentRecord:
        safe_name = Path(filename).name
        extension = Path(safe_name).suffix.lower()
        if not safe_name or extension not in SUPPORTED_EXTENSIONS:
            supported = ", ".join(sorted(SUPPORTED_EXTENSIONS))
            raise DocumentUploadError(
                f"不支持的文件类型: {extension or '无扩展名'}（支持 {supported}）"
            )
        if len(content) == 0:
            raise DocumentUploadError("文件内容为空")
        if len(content) > self.max_upload_bytes:
            raise DocumentUploadError(
                f"文件超过大小上限 {self.max_upload_bytes // (1024 * 1024)}MB"
            )
        if collection_id is not None and self.store.get_collection(collection_id) is None:
            raise DocumentUploadError(f"集合 {collection_id} 不存在")

        sha256 = hashlib.sha256(content).hexdigest()
        # 文件 hash 去重：同一内容不重复导入
        if self.store.find_by_sha256(sha256):
            raise DocumentUploadError("相同内容的文档已存在，跳过重复导入")

        document = self.store.add_document(
            name=safe_name,
            file_type=extension.lstrip(".").upper(),
            size=len(content),
            collection_id=collection_id,
            mime_type=_MIME_TYPES.get(extension),
            sha256=sha256,
            importance=importance,
        )
        original_path = self.originals_dir / f"{document.id}{extension}"
        original_path.write_bytes(content)
        self.store.set_original_path(document.id, str(original_path))

        self.store.set_document_status(document.id, status="parsing")
        try:
            chunks = self._parse_and_persist(document.id, original_path)
            document = self.store.set_document_status(
                document.id,
                status="indexed",
                chunk_count=len(chunks),
            )
            self.search_tool.refresh_index()
            self._write_index_meta()
            return document
        except Exception as exc:
            self.store.set_document_status(document.id, status="failed", error=str(exc))
            return self._require_document(document.id)

    def reindex(self, document_id: str) -> KnowledgeDocumentRecord:
        document = self.store.get_document(document_id)
        if document is None:
            raise KeyError(f"文档 {document_id} 不存在")
        if document.original_path is not None:
            original_path = Path(document.original_path)
        else:
            original_path = self.originals_dir / f"{document_id}.{document.type.lower()}"
        if not original_path.exists():
            self.store.set_document_status(document_id, status="failed", error="文件缺失")
            return self._require_document(document_id)
        self.store.set_document_status(document_id, status="parsing")
        try:
            chunks = self._parse_and_persist(document_id, original_path)
            self.search_tool.refresh_index()
            self._write_index_meta()
        except Exception as exc:
            self.store.set_document_status(document_id, status="failed", error=str(exc))
            return self._require_document(document_id)
        return self.store.set_document_status(document_id, status="indexed", chunk_count=len(chunks))

    def refresh(self) -> dict[str, int]:
        chunk_count = self.search_tool.refresh_index()
        self._write_index_meta()
        return {"chunk_count": chunk_count}

    _NOTE_COLLECTION = "对话保存"

    def summarize_document(self, subject: str = "", *, limit: int = 8) -> list[dict[str, object]]:
        """按 subject 找文档：名称/内容关键词匹配；未指定则取最近上传的文档。
        返回与 knowledge_search 相同的 matches 形状（供知识上下文/来源卡片复用）。"""
        documents = self.store.list_documents()
        if not documents:
            return []
        target = None
        subject = subject.strip().lower()
        if subject:
            for document in documents:
                name = (document.name or "").lower()
                stem = Path(document.name or "").stem.lower()
                if subject in name or subject in stem:
                    target = document
                    break
            if target is None:
                # 文件名没命中 → 扫分片内容（用户常用内容关键词指代文档）
                for document in documents:
                    chunks = self.store.list_chunks(document.id)
                    if any(subject in str(chunk["text"]).lower() for chunk in chunks[:5]):
                        target = document
                        break
            if target is None:
                return []  # 指定了主题但完全没找到 → 走「没找到」兜底
        if target is None:
            target = documents[0]  # list_documents 按 created_at DESC，即最近上传
        chunks = self.store.list_chunks(target.id)[:limit]
        source = target.original_path or ""
        return [
            {
                "chunk_id": str(chunk["id"]),
                "score": 1.0,
                "text": str(chunk["text"]),
                "source_path": str(source),
                "title": str(target.name),
                "heading_path": [],
            }
            for chunk in chunks
        ]

    def save_chat_note(self, title: str, content: str) -> str:
        """把聊天内容保存为知识条目（幂等：相同内容返回已有文档）。"""
        content = content.strip()
        title = (title or content[:24]).strip() or "知识条目"
        collection = self.store.get_collection_by_name(self._NOTE_COLLECTION)
        if collection is None:
            collection = self.store.create_collection(
                name=self._NOTE_COLLECTION,
                description="聊天中快捷保存的知识",
            )
        existing = self.store.find_by_sha256(hashlib.sha256(content.encode("utf-8")).hexdigest())
        if existing:
            return existing[0].id  # 幂等：重复保存同一内容
        safe_name = re.sub(r"[^\w\u4e00-\u9fff-]+", "_", title)[:60] or "note"
        document = self.upload(
            filename=f"{safe_name}.md",
            content=content.encode("utf-8"),
            collection_id=collection.id,
        )
        return document.id

    def delete(self, document_id: str) -> bool:
        document = self.store.get_document(document_id)
        if document is None:
            return False
        if document.original_path is not None:
            Path(document.original_path).unlink(missing_ok=True)
        if document.extracted_path is not None:
            Path(document.extracted_path).unlink(missing_ok=True)
        self.store.delete_document(document_id)  # chunks 级联删除
        self.search_tool.refresh_index()
        return True

    def _parse_and_persist(self, document_id: str, original_path: Path) -> list[dict[str, object]]:
        extension = original_path.suffix.lower()
        if extension in _TEXT_LOADER.supported_extensions:
            document = _TEXT_LOADER.load(original_path)
        elif extension in _PDF_LOADER.supported_extensions:
            document = _PDF_LOADER.load(original_path)
        elif extension in _DOCX_LOADER.supported_extensions:
            document = _DOCX_LOADER.load(original_path)
        else:
            raise DocumentUploadError(f"不支持的文件类型: {extension}")

        # extracted 文本落盘
        extracted_path = self.extracted_dir / f"{document_id}.txt"
        extracted_path.write_text(document.text, encoding="utf-8")
        self.store.set_extracted_path(document_id, str(extracted_path))

        chunker = MarkdownChunker(chunk_size=500, overlap=80)
        chunks = chunker.split(document)
        # The stored filename is an implementation detail. Keep citations and
        # retrieval metadata anchored to the filename the user actually chose.
        record = self._require_document(document_id)
        for chunk in chunks:
            chunk.metadata["source_path"] = record.name
            chunk.metadata["title"] = record.name
        chunk_rows = [
            {
                "id": chunk.chunk_id,
                "chunk_index": chunk.chunk_index,
                "text": chunk.text,
                "token_count": max(1, len(chunk.text) // 4),
                "embedding_id": self.embedding_id,
                "metadata_json": json.dumps(chunk.metadata, ensure_ascii=False),
            }
            for chunk in chunks
        ]
        self.store.replace_chunks(document_id, chunk_rows)
        self._embed_chunks(document_id, chunk_rows)
        return chunk_rows

    def _embed_chunks(self, document_id: str, chunk_rows: list[dict[str, object]]) -> None:
        """上传时即计算并落库 embedding；embedding 失败不阻断上传（可由重建 job 回填）。"""
        if self.embedding_provider is None or self.embedding_id is None:
            return
        try:
            vectors = self.embedding_provider.embed_documents([str(row["text"]) for row in chunk_rows])
        except Exception:
            return
        for row, vector in zip(chunk_rows, vectors, strict=True):
            self.store.save_chunk_embedding(str(row["id"]), self.embedding_id, vector)
        self.store.set_chunks_embedding_id(document_id, self.embedding_id)

    def rebuild_index(self, on_progress=None) -> dict[str, object]:
        """全量重建：回填缺失 embedding → 重建检索器 → 更新索引元数据。

        on_progress(done: int, total: int) 供异步 job 报告进度。
        """
        backfilled = 0
        if self.embedding_provider is not None and self.embedding_id is not None:
            missing = self.store.list_chunks_without_embedding(self.embedding_id)
            total = len(missing)
            for index, (chunk_id, text) in enumerate(missing):
                try:
                    vector = self.embedding_provider.embed_query(text)
                except Exception:
                    continue
                self.store.save_chunk_embedding(chunk_id, self.embedding_id, vector)
                backfilled += 1
                if on_progress is not None:
                    on_progress(index + 1, total)
        chunk_count = self.search_tool.refresh_index()
        self._write_index_meta()
        return {"chunk_count": chunk_count, "backfilled": backfilled}

    def _write_index_meta(self) -> None:
        self.store.set_index_meta(
            {
                "embedding_provider": self.embedding_provider.__class__.__name__ if self.embedding_provider else "none",
                "embedding_id": self.embedding_id or "",
                "chunk_strategy": self.chunk_strategy,
                "vector_store": "milvus" if getattr(self.search_tool, "milvus_enabled", False) else "memory",
                "last_refresh_at": datetime.now(UTC).isoformat(),
            }
        )

    def _require_document(self, document_id: str) -> KnowledgeDocumentRecord:
        document = self.store.get_document(document_id)
        if document is None:
            raise KeyError(f"文档 {document_id} 不存在")
        return document
