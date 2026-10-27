"""项目目录存储：用户添加的本地项目（目录引用，不复制文件）。

项目 = 一个目录路径 + 名称，持久化在 SQLite（projects 表）。
路径只存引用，文件仍在原处；Agent 通过受 allowed_roots 限制的
文件工具/API 访问项目内容。
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel


class ProjectRecord(BaseModel):
    id: str
    name: str
    path: str
    created_at: str
    updated_at: str


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ProjectStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    def _init_schema(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    path TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

    def list_projects(self) -> list[ProjectRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM projects ORDER BY updated_at DESC"
            ).fetchall()
        return [self._row_to_record(row) for row in rows]

    def get(self, project_id: str) -> ProjectRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM projects WHERE id = ?", (project_id,)
            ).fetchone()
        return self._row_to_record(row) if row is not None else None

    def get_by_path(self, path: str) -> ProjectRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM projects WHERE path = ?", (path,)
            ).fetchone()
        return self._row_to_record(row) if row is not None else None

    def add(self, *, name: str, path: str) -> ProjectRecord:
        now = _now_iso()
        project_id = f"project_{uuid.uuid4().hex}"
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO projects (id, name, path, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (project_id, name, path, now, now),
            )
        return ProjectRecord(id=project_id, name=name, path=path, created_at=now, updated_at=now)

    def remove(self, project_id: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute("DELETE FROM projects WHERE id = ?", (project_id,))
        return cursor.rowcount > 0

    def _row_to_record(self, row: sqlite3.Row) -> ProjectRecord:
        return ProjectRecord(
            id=row["id"],
            name=row["name"],
            path=row["path"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
