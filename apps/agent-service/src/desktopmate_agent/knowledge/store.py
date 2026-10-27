"""知识库文档与集合的 SQLite 存储。"""

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel

DocumentStatus = Literal["queued", "parsing", "indexed", "failed"]
SUPPORTED_EXTENSIONS = {".md", ".markdown", ".txt", ".pdf", ".docx"}


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class KnowledgeCollection(BaseModel):
    id: str
    name: str
    description: str
    created_at: str
    updated_at: str


class KnowledgeDocumentRecord(BaseModel):
    id: str
    name: str
    type: str
    size: int
    collection_id: str | None = None
    status: DocumentStatus
    chunk_count: int = 0
    error: str | None = None
    original_path: str | None = None
    extracted_path: str | None = None
    mime_type: str | None = None
    sha256: str | None = None
    source: str = "upload"
    importance: float = 0.5
    active: bool = True
    last_accessed: str
    created_at: str
    updated_at: str


class DocumentStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _init_schema(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS collections (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS documents (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    type TEXT NOT NULL,
                    size INTEGER NOT NULL,
                    collection_id TEXT REFERENCES collections(id) ON DELETE SET NULL,
                    status TEXT NOT NULL DEFAULT 'queued',
                    chunk_count INTEGER NOT NULL DEFAULT 0,
                    error TEXT,
                    original_path TEXT,
                    extracted_path TEXT,
                    mime_type TEXT,
                    sha256 TEXT,
                    source TEXT NOT NULL DEFAULT 'upload',
                    importance REAL NOT NULL DEFAULT 0.5,
                    active INTEGER NOT NULL DEFAULT 1,
                    last_accessed DATETIME DEFAULT CURRENT_TIMESTAMP,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS document_chunks (
                    id TEXT PRIMARY KEY,
                    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                    chunk_index INTEGER NOT NULL,
                    text TEXT NOT NULL,
                    token_count INTEGER NOT NULL DEFAULT 0,
                    embedding_id TEXT,
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS chunk_embeddings (
                    chunk_id TEXT PRIMARY KEY REFERENCES document_chunks(id) ON DELETE CASCADE,
                    embedding_id TEXT NOT NULL,
                    vector TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS index_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_chunks_document ON document_chunks(document_id, chunk_index)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_documents_collection ON documents(collection_id)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_documents_status ON documents(status)"
            )
            # 兼容旧库：补充 documents 新列
            existing_columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(documents)").fetchall()
            }
            for column, definition in {
                "original_path": "TEXT",
                "extracted_path": "TEXT",
                "mime_type": "TEXT",
                "sha256": "TEXT",
                "source": "TEXT NOT NULL DEFAULT 'upload'",
                "importance": "REAL NOT NULL DEFAULT 0.5",
                "active": "INTEGER NOT NULL DEFAULT 1",
                "last_accessed": "TEXT",
            }.items():
                if column not in existing_columns:
                    connection.execute(f"ALTER TABLE documents ADD COLUMN {column} {definition}")
            connection.execute(
                "UPDATE documents SET last_accessed = ? WHERE last_accessed IS NULL",
                (_now_iso(),),
            )

    # --- Collections ---

    def create_collection(self, *, name: str, description: str = "") -> KnowledgeCollection:
        collection_id = f"collection_{uuid4().hex}"
        now = _now_iso()
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO collections (id, name, description, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (collection_id, name, description, now, now),
            )
        return KnowledgeCollection(
            id=collection_id,
            name=name,
            description=description,
            created_at=now,
            updated_at=now,
        )

    def list_collections(self) -> list[KnowledgeCollection]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM collections ORDER BY created_at ASC").fetchall()
        return [self._collection_from_row(row) for row in rows if row is not None]

    def get_collection(self, collection_id: str) -> KnowledgeCollection | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM collections WHERE id = ?",
                (collection_id,),
            ).fetchone()
        return self._collection_from_row(row)

    def get_collection_by_name(self, name: str) -> KnowledgeCollection | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM collections WHERE name = ?",
                (name,),
            ).fetchone()
        return self._collection_from_row(row)

    def update_collection(
        self,
        collection_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
    ) -> KnowledgeCollection | None:
        updates: list[str] = []
        params: list[Any] = []
        if name is not None:
            updates.append("name = ?")
            params.append(name)
        if description is not None:
            updates.append("description = ?")
            params.append(description)
        if not updates:
            return self.get_collection(collection_id)
        updates.append("updated_at = ?")
        params.append(_now_iso())
        params.append(collection_id)
        with self._connect() as connection:
            cursor = connection.execute(
                f"UPDATE collections SET {', '.join(updates)} WHERE id = ?",
                params,
            )
        if cursor.rowcount == 0:
            return None
        return self.get_collection(collection_id)

    def delete_collection(self, collection_id: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute("DELETE FROM collections WHERE id = ?", (collection_id,))
        return cursor.rowcount > 0

    # --- Documents ---

    def add_document(
        self,
        *,
        name: str,
        file_type: str,
        size: int,
        collection_id: str | None = None,
        original_path: str | None = None,
        extracted_path: str | None = None,
        mime_type: str | None = None,
        sha256: str | None = None,
        source: str = "upload",
        importance: float = 0.5,
    ) -> KnowledgeDocumentRecord:
        document_id = f"doc_{uuid4().hex}"
        now = _now_iso()
        normalized_importance = max(0.0, min(float(importance), 1.0))
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO documents "
                "(id, name, type, size, collection_id, status, original_path, "
                " extracted_path, mime_type, sha256, source, importance, last_accessed, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, 'queued', ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    document_id,
                    name,
                    file_type,
                    size,
                    collection_id,
                    original_path,
                    extracted_path,
                    mime_type,
                    sha256,
                    source,
                    normalized_importance,
                    now,
                    now,
                    now,
                ),
            )
        return KnowledgeDocumentRecord(
            id=document_id,
            name=name,
            type=file_type,
            size=size,
            collection_id=collection_id,
            status="queued",
            original_path=original_path,
            extracted_path=extracted_path,
            mime_type=mime_type,
            sha256=sha256,
            source=source,
            importance=normalized_importance,
            active=True,
            last_accessed=now,
            created_at=now,
            updated_at=now,
        )

    def set_original_path(self, document_id: str, path: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE documents SET original_path = ?, updated_at = ? WHERE id = ?",
                (path, _now_iso(), document_id),
            )

    def set_extracted_path(self, document_id: str, path: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE documents SET extracted_path = ?, updated_at = ? WHERE id = ?",
                (path, _now_iso(), document_id),
            )

    def replace_chunks(
        self,
        document_id: str,
        chunks: list[dict[str, object]],
    ) -> None:
        """整文档替换 chunk：先删后插，保证与当前解析一致。"""
        with self._connect() as connection:
            connection.execute("DELETE FROM document_chunks WHERE document_id = ?", (document_id,))
            for chunk in chunks:
                connection.execute(
                    "INSERT INTO document_chunks "
                    "(id, document_id, chunk_index, text, token_count, embedding_id, metadata_json) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        chunk["id"],
                        document_id,
                        chunk["chunk_index"],
                        chunk["text"],
                        chunk.get("token_count", 0),
                        chunk.get("embedding_id"),
                        chunk.get("metadata_json", "{}"),
                    ),
                )

    def list_chunks(self, document_id: str) -> list[dict[str, object]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM document_chunks WHERE document_id = ? ORDER BY chunk_index",
                (document_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_documents(
        self,
        *,
        collection_id: str | None = None,
        status: str | None = None,
        query: str | None = None,
    ) -> list[KnowledgeDocumentRecord]:
        clauses: list[str] = []
        params: list[Any] = []
        if collection_id:
            clauses.append("collection_id = ?")
            params.append(collection_id)
        if status:
            clauses.append("status = ?")
            params.append(status)
        if query:
            clauses.append("name LIKE ?")
            params.append(f"%{query}%")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM documents {where} ORDER BY created_at DESC",
                params,
            ).fetchall()
        return [self._document_from_row(row) for row in rows if row is not None]

    def find_by_sha256(self, sha256: str) -> list[KnowledgeDocumentRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM documents WHERE sha256 = ?",
                (sha256,),
            ).fetchall()
        return [self._document_from_row(row) for row in rows if row is not None]

    def get_document(self, document_id: str) -> KnowledgeDocumentRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM documents WHERE id = ?",
                (document_id,),
            ).fetchone()
        return self._document_from_row(row)

    def set_document_status(
        self,
        document_id: str,
        *,
        status: DocumentStatus,
        chunk_count: int | None = None,
        error: str | None = None,
    ) -> KnowledgeDocumentRecord | None:
        updates = ["status = ?", "updated_at = ?"]
        params: list[Any] = [status, _now_iso()]
        if chunk_count is not None:
            updates.append("chunk_count = ?")
            params.append(chunk_count)
        if error is not None:
            updates.append("error = ?")
            params.append(error)
        params.append(document_id)
        with self._connect() as connection:
            cursor = connection.execute(
                f"UPDATE documents SET {', '.join(updates)} WHERE id = ?",
                params,
            )
        if cursor.rowcount == 0:
            return None
        return self.get_document(document_id)

    def delete_document(self, document_id: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute("DELETE FROM documents WHERE id = ?", (document_id,))
        return cursor.rowcount > 0

    def touch_documents(self, document_ids: set[str]) -> None:
        if not document_ids:
            return
        now = _now_iso()
        placeholders = ", ".join("?" for _ in document_ids)
        with self._connect() as connection:
            connection.execute(
                f"UPDATE documents SET last_accessed = ?, updated_at = ? WHERE id IN ({placeholders})",
                [now, now, *sorted(document_ids)],
            )

    def deactivate_stale_documents(self, *, cutoff: datetime, importance_threshold: float) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE documents
                SET active = 0, updated_at = ?
                WHERE active = 1
                  AND importance < ?
                  AND last_accessed < ?
                """,
                (_now_iso(), importance_threshold, cutoff.isoformat()),
            )
        return cursor.rowcount

    # --- S7: embedding 落库 + 索引元数据 ---

    def save_chunk_embedding(self, chunk_id: str, embedding_id: str, vector: list[float]) -> None:
        payload = json.dumps(vector, ensure_ascii=False)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO chunk_embeddings (chunk_id, embedding_id, vector, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(chunk_id) DO UPDATE SET
                    embedding_id = excluded.embedding_id,
                    vector = excluded.vector,
                    created_at = excluded.created_at
                """,
                (chunk_id, embedding_id, payload, datetime.now(UTC).isoformat()),
            )

    def set_chunks_embedding_id(self, document_id: str, embedding_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE document_chunks SET embedding_id = ? WHERE document_id = ?",
                (embedding_id, document_id),
            )

    def list_chunks_without_embedding(self, embedding_id: str) -> list[tuple[str, str]]:
        """返回 (chunk_id, text) 列表——无向量或 embedding_id 过期（provider 变更）的 chunk。"""
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT c.id, c.text FROM document_chunks c
                LEFT JOIN chunk_embeddings e ON e.chunk_id = c.id
                WHERE e.chunk_id IS NULL OR e.embedding_id != ?
                ORDER BY c.chunk_index
                """,
                (embedding_id,),
            ).fetchall()
        return [(row["id"], row["text"]) for row in rows]

    def load_chunks_with_embeddings(
        self,
        embedding_id: str,
    ) -> list[tuple[dict[str, object], list[float] | None]]:
        """加载 (chunk 行, vector)；无向量或向量过期（provider 变更）的 chunk vector 为 None。"""
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT c.id, c.document_id, c.chunk_index, c.text, c.metadata_json,
                       d.name AS document_name, d.original_path, d.extracted_path,
                       d.importance,
                       e.embedding_id AS stored_embedding_id, e.vector
                FROM document_chunks c
                JOIN documents d ON d.id = c.document_id
                LEFT JOIN chunk_embeddings e ON e.chunk_id = c.id
                WHERE d.active = 1 AND d.status = 'indexed'
                ORDER BY c.document_id, c.chunk_index
                """
            ).fetchall()
        result: list[tuple[dict[str, object], list[float] | None]] = []
        for row in rows:
            try:
                metadata = json.loads(row["metadata_json"] or "{}")
            except json.JSONDecodeError:
                metadata = {}
            # Normalize display metadata at read time too. This repairs older
            # indexes that stored the internal document id as the citation title.
            metadata["title"] = str(row["document_name"])
            metadata["source_path"] = str(
                row["original_path"] or row["extracted_path"] or row["document_name"]
            )
            metadata["importance"] = float(row["importance"] or 0.5)
            vector: list[float] | None = None
            if row["vector"] is not None and row["stored_embedding_id"] == embedding_id:
                try:
                    vector = json.loads(row["vector"])
                except (json.JSONDecodeError, TypeError):
                    vector = None
            result.append(
                (
                    {
                        "id": row["id"],
                        "document_id": row["document_id"],
                        "chunk_index": row["chunk_index"],
                        "text": row["text"],
                        "metadata": metadata,
                    },
                    vector,
                )
            )
        return result

    def count_documents(self) -> int:
        with self._connect() as connection:
            row = connection.execute("SELECT COUNT(*) AS n FROM documents").fetchone()
        return int(row["n"])

    def count_chunks(self) -> int:
        with self._connect() as connection:
            row = connection.execute("SELECT COUNT(*) AS n FROM document_chunks").fetchone()
        return int(row["n"])

    def get_index_meta(self) -> dict[str, str]:
        with self._connect() as connection:
            rows = connection.execute("SELECT key, value FROM index_meta").fetchall()
        return {row["key"]: row["value"] for row in rows}

    def set_index_meta(self, values: dict[str, str]) -> None:
        with self._connect() as connection:
            for key, value in values.items():
                connection.execute(
                    """
                    INSERT INTO index_meta (key, value) VALUES (?, ?)
                    ON CONFLICT(key) DO UPDATE SET value = excluded.value
                    """,
                    (key, value),
                )

    def _collection_from_row(self, row: sqlite3.Row | None) -> KnowledgeCollection | None:
        if row is None:
            return None
        return KnowledgeCollection(
            id=row["id"],
            name=row["name"],
            description=row["description"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _document_from_row(self, row: sqlite3.Row | None) -> KnowledgeDocumentRecord | None:
        if row is None:
            return None
        return KnowledgeDocumentRecord(
            id=row["id"],
            name=row["name"],
            type=row["type"],
            size=row["size"],
            collection_id=row["collection_id"],
            status=row["status"],
            chunk_count=row["chunk_count"],
            error=row["error"],
            original_path=row["original_path"],
            extracted_path=row["extracted_path"],
            mime_type=row["mime_type"],
            sha256=row["sha256"],
            source=row["source"],
            importance=row["importance"],
            active=bool(row["active"]),
            last_accessed=row["last_accessed"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
