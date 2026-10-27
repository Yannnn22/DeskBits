"""用户画像事实存储：profile_facts 表（与 sessions/knowledge/memory 同库）。

画像不是自由文本，而是从 memory_items 聚合出的结构化事实，每条保留来源
（source_memory_id）与置信度，便于解释与更新。
"""

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, Field


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class ProfileFact(BaseModel):
    id: str
    field: str
    value: str
    confidence: float = Field(ge=0.0, le=1.0)
    source_memory_id: str | None = None
    updated_at: str


class ProfileFactsStore:
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
                CREATE TABLE IF NOT EXISTS profile_facts (
                    id TEXT PRIMARY KEY,
                    field TEXT NOT NULL,
                    value TEXT NOT NULL,
                    confidence REAL NOT NULL DEFAULT 1.0,
                    source_memory_id TEXT,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_profile_facts_field ON profile_facts(field)"
            )

    def upsert_fact(
        self,
        *,
        field: str,
        value: str,
        confidence: float = 1.0,
        source_memory_id: str | None = None,
    ) -> ProfileFact:
        now = _now_iso()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT id FROM profile_facts WHERE field = ?",
                (field,),
            ).fetchone()
            if row is None:
                fact_id = f"pf_{uuid4().hex}"
                connection.execute(
                    "INSERT INTO profile_facts (id, field, value, confidence, source_memory_id, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (fact_id, field, value, confidence, source_memory_id, now),
                )
            else:
                fact_id = row["id"]
                connection.execute(
                    "UPDATE profile_facts SET value = ?, confidence = ?, source_memory_id = ?, updated_at = ? "
                    "WHERE id = ?",
                    (value, confidence, source_memory_id, now, fact_id),
                )
        return ProfileFact(
            id=fact_id,
            field=field,
            value=value,
            confidence=confidence,
            source_memory_id=source_memory_id,
            updated_at=now,
        )

    def list_facts(self) -> list[ProfileFact]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM profile_facts ORDER BY field"
            ).fetchall()
        return [self._row_to_fact(row) for row in rows]

    def get_fact(self, field: str) -> ProfileFact | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM profile_facts WHERE field = ?",
                (field,),
            ).fetchone()
        return self._row_to_fact(row) if row is not None else None

    def clear(self) -> int:
        with self._connect() as connection:
            cursor = connection.execute("DELETE FROM profile_facts")
        return cursor.rowcount

    def _row_to_fact(self, row: sqlite3.Row) -> ProfileFact:
        return ProfileFact(
            id=row["id"],
            field=row["field"],
            value=row["value"],
            confidence=row["confidence"],
            source_memory_id=row["source_memory_id"],
            updated_at=row["updated_at"],
        )
