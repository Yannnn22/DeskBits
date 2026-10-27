"""SQLite persistence for user-confirmed workspace artifacts and draft revisions."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field


ArtifactState = Literal["draft", "saved", "archived"]
VersionState = Literal["draft", "saved", "superseded"]


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class ArtifactVersion(BaseModel):
    version: int
    state: VersionState
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str


class ArtifactRecord(BaseModel):
    id: str
    kind: str
    title: str
    state: ArtifactState
    source_session_id: str | None = None
    external_ref: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str
    updated_at: str
    current: ArtifactVersion | None = None
    pending: ArtifactVersion | None = None

    def to_dict(self, *, include_pending: bool = False) -> dict[str, Any]:
        # 工作区读取已确认版本。候选版本只在生成后的当前对话或显式请求时显示，
        # 防止用户尚未确认就把工作区正文“悄悄替换”。
        if self.state == "draft" or include_pending:
            display = self.pending or self.current
        else:
            display = self.current
        return {
            "id": self.id,
            "kind": self.kind,
            "title": self.title,
            "state": self.state,
            "source_session_id": self.source_session_id,
            "external_ref": self.external_ref,
            "metadata": self.metadata,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "content": display.content if display else "",
            "version": display.version if display else 0,
            "version_state": display.state if display else None,
            "pending_version": self.pending.version if self.pending else None,
            "current_version": self.current.version if self.current else None,
        }


class ArtifactStore:
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
                CREATE TABLE IF NOT EXISTS artifacts (
                    id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    title TEXT NOT NULL,
                    state TEXT NOT NULL,
                    source_session_id TEXT,
                    external_ref TEXT,
                    metadata TEXT NOT NULL DEFAULT '{}',
                    current_version INTEGER,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS artifact_versions (
                    artifact_id TEXT NOT NULL REFERENCES artifacts(id) ON DELETE CASCADE,
                    version INTEGER NOT NULL,
                    state TEXT NOT NULL,
                    content TEXT NOT NULL,
                    metadata TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (artifact_id, version)
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_artifacts_updated ON artifacts(updated_at DESC)"
            )

    def create_draft(
        self,
        *,
        kind: str,
        title: str,
        content: str,
        source_session_id: str | None = None,
        external_ref: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ArtifactRecord:
        artifact_id = f"artifact_{uuid4().hex}"
        now = _now_iso()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO artifacts (
                    id, kind, title, state, source_session_id, external_ref, metadata,
                    current_version, created_at, updated_at
                ) VALUES (?, ?, ?, 'draft', ?, ?, ?, NULL, ?, ?)
                """,
                (
                    artifact_id,
                    kind[:64],
                    title[:200],
                    source_session_id,
                    external_ref,
                    json.dumps(metadata or {}, ensure_ascii=False),
                    now,
                    now,
                ),
            )
            connection.execute(
                """
                INSERT INTO artifact_versions (artifact_id, version, state, content, metadata, created_at)
                VALUES (?, 1, 'draft', ?, ?, ?)
                """,
                (artifact_id, content, json.dumps(metadata or {}, ensure_ascii=False), now),
            )
        record = self.get(artifact_id)
        assert record is not None
        return record

    def list(self, *, include_drafts: bool = False) -> list[ArtifactRecord]:
        query = "SELECT id FROM artifacts"
        if not include_drafts:
            query += " WHERE state = 'saved'"
        query += " ORDER BY updated_at DESC"
        with self._connect() as connection:
            rows = connection.execute(query).fetchall()
        return [record for row in rows if (record := self.get(str(row["id"]))) is not None]

    def get(self, artifact_id: str) -> ArtifactRecord | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM artifacts WHERE id = ?", (artifact_id,)).fetchone()
            if row is None:
                return None
            versions = connection.execute(
                "SELECT * FROM artifact_versions WHERE artifact_id = ? ORDER BY version DESC",
                (artifact_id,),
            ).fetchall()
        current_version = int(row["current_version"]) if row["current_version"] is not None else None
        current: ArtifactVersion | None = None
        pending: ArtifactVersion | None = None
        for version_row in versions:
            version = self._version_from_row(version_row)
            if current_version == version.version:
                current = version
            if pending is None and version.state == "draft":
                pending = version
        return ArtifactRecord(
            id=str(row["id"]),
            kind=str(row["kind"]),
            title=str(row["title"]),
            state=str(row["state"]),  # type: ignore[arg-type]
            source_session_id=row["source_session_id"],
            external_ref=row["external_ref"],
            metadata=self._json_dict(row["metadata"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            current=current,
            pending=pending,
        )

    def confirm(self, artifact_id: str) -> ArtifactRecord | None:
        record = self.get(artifact_id)
        if record is None or record.pending is None:
            return None
        now = _now_iso()
        with self._connect() as connection:
            if record.current is not None:
                connection.execute(
                    "UPDATE artifact_versions SET state = 'superseded' WHERE artifact_id = ? AND version = ?",
                    (artifact_id, record.current.version),
                )
            connection.execute(
                "UPDATE artifact_versions SET state = 'saved' WHERE artifact_id = ? AND version = ?",
                (artifact_id, record.pending.version),
            )
            connection.execute(
                """
                UPDATE artifacts
                SET state = 'saved', current_version = ?, updated_at = ?
                WHERE id = ?
                """,
                (record.pending.version, now, artifact_id),
            )
        return self.get(artifact_id)

    def revise(
        self,
        artifact_id: str,
        *,
        content: str,
        metadata: dict[str, Any] | None = None,
        title: str | None = None,
    ) -> ArtifactRecord | None:
        record = self.get(artifact_id)
        if record is None:
            return None
        now = _now_iso()
        with self._connect() as connection:
            # 一个成品同时只能保留一个待确认版本；否则确认最新版后，旧草稿会
            # 再次被当作 pending 版本展示。
            connection.execute(
                "UPDATE artifact_versions SET state = 'superseded' WHERE artifact_id = ? AND state = 'draft'",
                (artifact_id,),
            )
            row = connection.execute(
                "SELECT COALESCE(MAX(version), 0) AS latest FROM artifact_versions WHERE artifact_id = ?",
                (artifact_id,),
            ).fetchone()
            next_version = int(row["latest"]) + 1
            connection.execute(
                """
                INSERT INTO artifact_versions (artifact_id, version, state, content, metadata, created_at)
                VALUES (?, ?, 'draft', ?, ?, ?)
                """,
                (artifact_id, next_version, content, json.dumps(metadata or {}, ensure_ascii=False), now),
            )
            if title:
                connection.execute(
                    "UPDATE artifacts SET title = ?, updated_at = ? WHERE id = ?",
                    (title[:200], now, artifact_id),
                )
            else:
                connection.execute("UPDATE artifacts SET updated_at = ? WHERE id = ?", (now, artifact_id))
        return self.get(artifact_id)

    def archive(self, artifact_id: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE artifacts SET state = 'archived', updated_at = ? WHERE id = ?",
                (_now_iso(), artifact_id),
            )
        return cursor.rowcount > 0

    @staticmethod
    def _json_dict(value: object) -> dict[str, Any]:
        try:
            parsed = json.loads(str(value))
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    def _version_from_row(self, row: sqlite3.Row) -> ArtifactVersion:
        return ArtifactVersion(
            version=int(row["version"]),
            state=str(row["state"]),  # type: ignore[arg-type]
            content=str(row["content"]),
            metadata=self._json_dict(row["metadata"]),
            created_at=str(row["created_at"]),
        )
