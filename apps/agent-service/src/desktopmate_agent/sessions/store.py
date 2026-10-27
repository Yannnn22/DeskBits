"""会话与消息的 SQLite 存储。

与 Memory / Trace 分开，只保存会话元数据与消息历史。
使用标准库 sqlite3，每次操作独立连接，避免跨线程共享连接。
"""

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

SessionMode = Literal["chat", "agent", "plan"]
DEFAULT_TITLE = "New Conversation"
MAX_TITLE_LENGTH = 200


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class SessionRecord(BaseModel):
    id: str
    title: str
    mode: SessionMode
    archived: bool
    project_id: str | None = None
    created_at: str
    updated_at: str


class SessionMessage(BaseModel):
    id: str
    session_id: str
    role: Literal["user", "assistant", "system"]
    text: str
    created_at: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class SessionStore:
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
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    mode TEXT NOT NULL DEFAULT 'agent',
                    archived INTEGER NOT NULL DEFAULT 0,
                    project_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            # 旧库迁移：无 project_id 列时补列（不破坏既有数据）
            columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(sessions)")
            }
            if "project_id" not in columns:
                connection.execute("ALTER TABLE sessions ADD COLUMN project_id TEXT")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                    role TEXT NOT NULL,
                    text TEXT NOT NULL,
                    metadata TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, created_at)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_sessions_project ON sessions(project_id)"
            )

    def create_session(
        self,
        *,
        title: str = DEFAULT_TITLE,
        mode: SessionMode = "agent",
        session_id: str | None = None,
        project_id: str | None = None,
    ) -> SessionRecord:
        active_id = session_id or f"session_{uuid4().hex}"
        now = _now_iso()
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO sessions (id, title, mode, archived, project_id, created_at, updated_at) "
                "VALUES (?, ?, ?, 0, ?, ?, ?)",
                (active_id, title[:MAX_TITLE_LENGTH], mode, project_id, now, now),
            )
        return SessionRecord(
            id=active_id,
            title=title,
            mode=mode,
            archived=False,
            project_id=project_id,
            created_at=now,
            updated_at=now,
        )

    def list_sessions_by_project(self, project_id: str) -> list[SessionRecord]:
        """列出某个项目下的所有会话（按更新时间倒序）。"""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM sessions WHERE project_id = ? ORDER BY updated_at DESC",
                (project_id,),
            ).fetchall()
        return [self._session_from_row(row) for row in rows]

    def ensure_session(
        self, session_id: str, *, mode: SessionMode = "agent", project_id: str | None = None
    ) -> SessionRecord:
        existing = self.get_session(session_id)
        if existing is not None:
            return existing
        return self.create_session(session_id=session_id, mode=mode, project_id=project_id)

    def get_session(self, session_id: str) -> SessionRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
        return self._session_from_row(row)

    def list_sessions(self, *, scope: Literal["recent", "all", "archived"] = "recent") -> list[SessionRecord]:
        query = "SELECT * FROM sessions"
        params: tuple[Any, ...] = ()
        if scope == "recent":
            query += " WHERE archived = 0"
        elif scope == "archived":
            query += " WHERE archived = 1"
        query += " ORDER BY updated_at DESC"
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [self._session_from_row(row) for row in rows if row is not None]

    def update_session(
        self,
        session_id: str,
        *,
        title: str | None = None,
        archived: bool | None = None,
    ) -> SessionRecord | None:
        updates: list[str] = []
        params: list[Any] = []
        if title is not None:
            updates.append("title = ?")
            params.append(title[:MAX_TITLE_LENGTH])
        if archived is not None:
            updates.append("archived = ?")
            params.append(1 if archived else 0)
        if not updates:
            return self.get_session(session_id)
        updates.append("updated_at = ?")
        params.append(_now_iso())
        params.append(session_id)
        with self._connect() as connection:
            cursor = connection.execute(
                f"UPDATE sessions SET {', '.join(updates)} WHERE id = ?",
                params,
            )
        if cursor.rowcount == 0:
            return None
        return self.get_session(session_id)

    def delete_session(self, session_id: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        return cursor.rowcount > 0

    def add_message(
        self,
        session_id: str,
        *,
        role: Literal["user", "assistant", "system"],
        text: str,
        metadata: dict[str, Any] | None = None,
    ) -> SessionMessage:
        message_id = f"msg_{uuid4().hex}"
        now = _now_iso()
        with self._connect() as connection:
            cursor = connection.execute(
                "INSERT INTO messages (id, session_id, role, text, metadata, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (message_id, session_id, role, text, json.dumps(metadata or {}, ensure_ascii=False), now),
            )
            if cursor.rowcount == 0:
                raise KeyError(f"Session '{session_id}' does not exist")
            connection.execute("UPDATE sessions SET updated_at = ? WHERE id = ?", (now, session_id))
        return SessionMessage(
            id=message_id,
            session_id=session_id,
            role=role,
            text=text,
            created_at=now,
            metadata=metadata or {},
        )

    def list_messages(self, session_id: str) -> list[SessionMessage]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM messages WHERE session_id = ? ORDER BY created_at ASC, id ASC",
                (session_id,),
            ).fetchall()
        messages: list[SessionMessage] = []
        for row in rows:
            message = self._message_from_row(row)
            if message is not None:
                messages.append(message)
        return messages

    def _session_from_row(self, row: sqlite3.Row | None) -> SessionRecord | None:
        if row is None:
            return None
        # 旧库行可能没有 project_id 列（迁移前创建），用 keys() 容错
        project_id = None
        try:
            if "project_id" in row.keys():
                project_id = row["project_id"]
        except Exception:
            project_id = None
        return SessionRecord(
            id=row["id"],
            title=row["title"],
            mode=row["mode"] if row["mode"] in {"chat", "agent", "plan"} else "agent",
            archived=bool(row["archived"]),
            project_id=project_id,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _message_from_row(self, row: sqlite3.Row | None) -> SessionMessage | None:
        if row is None:
            return None
        try:
            metadata = json.loads(row["metadata"])
        except json.JSONDecodeError:
            metadata = {}
        return SessionMessage(
            id=row["id"],
            session_id=row["session_id"],
            role=row["role"],
            text=row["text"],
            created_at=row["created_at"],
            metadata=metadata if isinstance(metadata, dict) else {},
        )
