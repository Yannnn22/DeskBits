import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from desktopmate_agent.memory.models import MemoryEntry, MemoryScope, MemorySensitivity, MemoryStatus


class JsonlMemoryStore:
    def __init__(self, memory_dir: Path) -> None:
        self.memory_dir = memory_dir
        self.memory_dir.mkdir(parents=True, exist_ok=True)

    def append(self, entry: MemoryEntry) -> None:
        path = self._path_for_entry(entry)
        with path.open("a", encoding="utf-8") as file:
            file.write(entry.model_dump_json() + "\n")

    def upsert(self, entry: MemoryEntry) -> bool:
        path = self._path_for_entry(entry)
        entries = self._read_path(path)
        kept_entries = [existing for existing in entries if existing.key != entry.key]
        updated = len(kept_entries) != len(entries)
        with path.open("w", encoding="utf-8") as file:
            for kept_entry in [*kept_entries, entry]:
                file.write(kept_entry.model_dump_json() + "\n")
        return updated

    def read_session(self, session_id: str) -> list[MemoryEntry]:
        return self.read_scope(MemoryScope.SESSION, session_id)

    def read_scope(self, scope: MemoryScope, subject_id: str) -> list[MemoryEntry]:
        path = self._path_for_scope(scope, subject_id)
        return self._read_path(path)

    def read_context(
        self,
        *,
        session_id: str,
        user_id: str = "default_user",
        project_id: str | None = None,
    ) -> list[MemoryEntry]:
        entries = [
            *self.read_scope(MemoryScope.SESSION, session_id),
            *self.read_scope(MemoryScope.USER, user_id),
        ]
        if project_id:
            entries.extend(self.read_scope(MemoryScope.PROJECT, project_id))
        return [entry for entry in entries if not self._is_expired(entry)]

    def delete(self, scope: MemoryScope, subject_id: str, key: str) -> int:
        path = self._path_for_scope(scope, subject_id)
        entries = self._read_path(path)
        kept_entries = [entry for entry in entries if entry.key != key]
        deleted_count = len(entries) - len(kept_entries)
        if deleted_count == 0:
            return 0
        with path.open("w", encoding="utf-8") as file:
            for entry in kept_entries:
                file.write(entry.model_dump_json() + "\n")
        return deleted_count

    def _read_path(self, path: Path) -> list[MemoryEntry]:
        if not path.exists():
            return []
        entries: list[MemoryEntry] = []
        with path.open("r", encoding="utf-8") as file:
            for line in file:
                if line.strip():
                    entry = MemoryEntry.model_validate(json.loads(line))
                    if not self._is_expired(entry):
                        entries.append(entry)
        return entries

    def _path_for_session(self, session_id: str) -> Path:
        return self._path_for_scope(MemoryScope.SESSION, session_id)

    def _path_for_entry(self, entry: MemoryEntry) -> Path:
        return self._path_for_scope(entry.scope, entry.subject_id or "")

    def _path_for_scope(self, scope: MemoryScope, subject_id: str) -> Path:
        safe_subject_id = subject_id.replace("/", "_")
        return self.memory_dir / f"{scope.value}-{safe_subject_id}.jsonl"

    def _is_expired(self, entry: MemoryEntry) -> bool:
        return entry.expires_at is not None and entry.expires_at <= datetime.now(UTC)



class SqliteMemoryStore:
    """SQLite 记忆存储：memory_items 当前快照 + memory_events 审计日志。

    与 JsonlMemoryStore 接口一致，支持 scope 读写、upsert、delete。
    删除采用 status='deleted' 软标记，便于审计与回溯。
    """

    def __init__(self, db_path: Path, events_path: Path | None = None) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.events_path = events_path
        if events_path is not None:
            events_path.parent.mkdir(parents=True, exist_ok=True)
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
                CREATE TABLE IF NOT EXISTS memory_items (
                    id TEXT PRIMARY KEY,
                    scope TEXT NOT NULL,
                    subject_id TEXT NOT NULL,
                    session_id TEXT,
                    key TEXT NOT NULL,
                    value TEXT NOT NULL,
                    confidence REAL NOT NULL DEFAULT 1.0,
                    source TEXT NOT NULL DEFAULT 'manual',
                    sensitivity TEXT NOT NULL DEFAULT 'normal',
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    expires_at TEXT
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS memory_events (
                    id TEXT PRIMARY KEY,
                    item_id TEXT,
                    action TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_memory_scope ON memory_items(scope, subject_id)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_memory_status ON memory_items(status)"
            )

    def append(self, entry: MemoryEntry) -> None:
        self.upsert(entry)

    def upsert(self, entry: MemoryEntry) -> bool:
        now = _now_iso()
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT id FROM memory_items WHERE scope = ? AND subject_id = ? AND key = ? "
                "AND status = 'active'",
                (entry.scope.value, entry.subject_id, entry.key),
            ).fetchall()
            updated = len(existing) > 0
            if updated:
                for row in existing:
                    connection.execute(
                        "UPDATE memory_items SET status = 'deleted', updated_at = ? WHERE id = ?",
                        (now, row["id"]),
                    )
                    self._log_event(
                        connection,
                        row["id"],
                        "supersede",
                        {"key": entry.key, "scope": entry.scope.value},
                    )
            item_id = entry.id or f"mem_{uuid4().hex}"
            connection.execute(
                "INSERT INTO memory_items "
                "(id, scope, subject_id, session_id, key, value, confidence, source, "
                " sensitivity, status, created_at, updated_at, expires_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    item_id,
                    entry.scope.value,
                    entry.subject_id,
                    entry.session_id,
                    entry.key,
                    entry.value,
                    entry.confidence,
                    entry.source,
                    entry.sensitivity.value,
                    entry.status.value,
                    entry.created_at.isoformat(),
                    entry.updated_at.isoformat(),
                    entry.expires_at.isoformat() if entry.expires_at else None,
                ),
            )
            self._log_event(
                connection,
                item_id,
                "upsert",
                {"key": entry.key, "scope": entry.scope.value, "subject_id": entry.subject_id},
            )
        return updated

    def get_candidate(self, candidate_id: str, *, session_id: str) -> MemoryEntry | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM memory_items WHERE id = ? AND session_id = ? AND status = 'candidate'",
                (candidate_id, session_id),
            ).fetchone()
        return self._row_to_entry(row)

    def resolve_candidate(
        self,
        candidate_id: str,
        *,
        session_id: str,
        approved: bool,
    ) -> MemoryEntry | None:
        candidate = self.get_candidate(candidate_id, session_id=session_id)
        if candidate is None:
            return None
        now = _now_iso()
        if approved:
            active = candidate.model_copy(
                update={
                    "id": None,
                    "status": MemoryStatus.ACTIVE,
                    "source": "candidate_approved",
                    "updated_at": datetime.now(UTC),
                }
            )
            self.upsert(active)
            status = MemoryStatus.DELETED
            action = "approve"
        else:
            active = candidate
            status = MemoryStatus.REJECTED
            action = "reject"
        with self._connect() as connection:
            connection.execute(
                "UPDATE memory_items SET status = ?, updated_at = ? WHERE id = ?",
                (status.value, now, candidate_id),
            )
            self._log_event(connection, candidate_id, action, {"session_id": session_id})
        return active if approved else candidate

    def read_session(self, session_id: str) -> list[MemoryEntry]:
        return self.read_scope(MemoryScope.SESSION, session_id)

    def read_scope(self, scope: MemoryScope, subject_id: str) -> list[MemoryEntry]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM memory_items WHERE scope = ? AND subject_id = ? AND status = 'active' "
                "ORDER BY updated_at DESC",
                (scope.value, subject_id),
            ).fetchall()
        return [
            entry
            for row in rows
            if (entry := self._row_to_entry(row)) is not None and not self._is_expired(entry)
        ]

    def read_context(
        self,
        *,
        session_id: str,
        user_id: str = "default_user",
        project_id: str | None = None,
    ) -> list[MemoryEntry]:
        entries = [
            *self.read_scope(MemoryScope.SESSION, session_id),
            *self.read_scope(MemoryScope.USER, user_id),
        ]
        if project_id:
            entries.extend(self.read_scope(MemoryScope.PROJECT, project_id))
        return [entry for entry in entries if not self._is_expired(entry)]

    def delete(self, scope: MemoryScope, subject_id: str, key: str) -> int:
        now = _now_iso()
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id FROM memory_items WHERE scope = ? AND subject_id = ? AND key = ? "
                "AND status = 'active'",
                (scope.value, subject_id, key),
            ).fetchall()
            for row in rows:
                connection.execute(
                    "UPDATE memory_items SET status = 'deleted', updated_at = ? WHERE id = ?",
                    (now, row["id"]),
                )
                self._log_event(
                    connection,
                    row["id"],
                    "delete",
                    {"key": key, "scope": scope.value, "subject_id": subject_id},
                )
        return len(rows)

    def list_events(self, limit: int = 200) -> list[dict[str, object]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM memory_events ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def _log_event(
        self,
        connection: sqlite3.Connection,
        item_id: str,
        action: str,
        payload: dict[str, object],
    ) -> None:
        now = _now_iso()
        connection.execute(
            "INSERT INTO memory_events (id, item_id, action, payload_json, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                f"mev_{uuid4().hex}",
                item_id,
                action,
                json.dumps(payload, ensure_ascii=False, default=str),
                now,
            ),
        )
        if self.events_path is not None:
            with self.events_path.open("a", encoding="utf-8") as file:
                file.write(
                    json.dumps(
                        {
                            "event_id": f"mev_{uuid4().hex}",
                            "item_id": item_id,
                            "action": action,
                            "payload": payload,
                            "created_at": now,
                        },
                        ensure_ascii=False,
                        default=str,
                    )
                    + "\n"
                )

    def _row_to_entry(self, row: sqlite3.Row) -> MemoryEntry | None:
        try:
            return MemoryEntry(
                id=row["id"],
                scope=MemoryScope(row["scope"]),
                subject_id=row["subject_id"],
                session_id=row["session_id"],
                key=row["key"],
                value=row["value"],
                confidence=row["confidence"],
                source=row["source"],
                sensitivity=MemorySensitivity(row["sensitivity"]),
                status=MemoryStatus(row["status"]),
                created_at=_parse_dt(row["created_at"]),
                updated_at=_parse_dt(row["updated_at"]),
                expires_at=_parse_dt(row["expires_at"]) if row["expires_at"] else None,
            )
        except (ValueError, KeyError):
            return None

    def _is_expired(self, entry: MemoryEntry) -> bool:
        return entry.expires_at is not None and entry.expires_at <= datetime.now(UTC)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value)




class RedisMemoryStore:
    def __init__(
        self,
        *,
        redis_url: str = "redis://127.0.0.1:16379/0",
        key_prefix: str = "desktopmate:memory",
        redis_client: Any | None = None,
    ) -> None:
        self.key_prefix = key_prefix
        if redis_client is None:
            try:
                from redis import Redis
            except ImportError as exc:
                raise RuntimeError(
                    "redis package is required for RedisMemoryStore. "
                    "Install it or use JsonlMemoryStore."
                ) from exc
            redis_client = Redis.from_url(redis_url, decode_responses=True)
        self.redis_client = redis_client

    def append(self, entry: MemoryEntry) -> None:
        self.redis_client.rpush(self._key_for_entry(entry), entry.model_dump_json())

    def upsert(self, entry: MemoryEntry) -> bool:
        redis_key = self._key_for_entry(entry)
        entries = self._read_key(redis_key)
        kept_entries = [existing for existing in entries if existing.key != entry.key]
        updated = len(kept_entries) != len(entries)
        self._replace_key(redis_key, [*kept_entries, entry])
        return updated

    def read_session(self, session_id: str) -> list[MemoryEntry]:
        return self.read_scope(MemoryScope.SESSION, session_id)

    def read_scope(self, scope: MemoryScope, subject_id: str) -> list[MemoryEntry]:
        return self._read_key(self._key_for_scope(scope, subject_id))

    def read_context(
        self,
        *,
        session_id: str,
        user_id: str = "default_user",
        project_id: str | None = None,
    ) -> list[MemoryEntry]:
        entries = [
            *self.read_scope(MemoryScope.SESSION, session_id),
            *self.read_scope(MemoryScope.USER, user_id),
        ]
        if project_id:
            entries.extend(self.read_scope(MemoryScope.PROJECT, project_id))
        return [entry for entry in entries if not self._is_expired(entry)]

    def delete(self, scope: MemoryScope, subject_id: str, key: str) -> int:
        redis_key = self._key_for_scope(scope, subject_id)
        entries = self._read_key(redis_key)
        kept_entries = [entry for entry in entries if entry.key != key]
        deleted_count = len(entries) - len(kept_entries)
        if deleted_count:
            self._replace_key(redis_key, kept_entries)
        return deleted_count

    def _read_key(self, redis_key: str) -> list[MemoryEntry]:
        entries: list[MemoryEntry] = []
        for raw_entry in self.redis_client.lrange(redis_key, 0, -1):
            entry = MemoryEntry.model_validate(json.loads(raw_entry))
            if not self._is_expired(entry):
                entries.append(entry)
        return entries

    def _replace_key(self, redis_key: str, entries: list[MemoryEntry]) -> None:
        self.redis_client.delete(redis_key)
        for entry in entries:
            self.redis_client.rpush(redis_key, entry.model_dump_json())

    def _key_for_entry(self, entry: MemoryEntry) -> str:
        return self._key_for_scope(entry.scope, entry.subject_id or "")

    def _key_for_scope(self, scope: MemoryScope, subject_id: str) -> str:
        safe_subject_id = subject_id.replace("/", "_")
        return f"{self.key_prefix}:{scope.value}:{safe_subject_id}"

    def _is_expired(self, entry: MemoryEntry) -> bool:
        return entry.expires_at is not None and entry.expires_at <= datetime.now(UTC)
