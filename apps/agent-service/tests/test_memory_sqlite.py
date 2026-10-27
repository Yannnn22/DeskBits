import json
import sqlite3
from pathlib import Path

from desktopmate_agent.memory.models import MemoryEntry, MemoryScope, MemorySensitivity, MemoryStatus
from desktopmate_agent.memory.store import SqliteMemoryStore


def make_entry(**overrides) -> MemoryEntry:
    defaults = {
        "scope": MemoryScope.USER,
        "subject_id": "user_1",
        "key": "language",
        "value": "中文",
        "source": "explicit",
    }
    defaults.update(overrides)
    return MemoryEntry(**defaults)


def test_sqlite_memory_upsert_and_read(tmp_path: Path):
    store = SqliteMemoryStore(tmp_path / "desktopmate.db")

    assert store.upsert(make_entry()) is False
    entries = store.read_scope(MemoryScope.USER, "user_1")

    assert len(entries) == 1
    assert entries[0].key == "language"
    assert entries[0].value == "中文"


def test_sqlite_memory_upsert_supersedes_old_version(tmp_path: Path):
    store = SqliteMemoryStore(tmp_path / "desktopmate.db")
    store.upsert(make_entry(value="English"))

    updated = store.upsert(make_entry(value="中文"))

    assert updated is True
    entries = store.read_scope(MemoryScope.USER, "user_1")
    assert len(entries) == 1
    assert entries[0].value == "中文"


def test_sqlite_memory_delete_is_soft(tmp_path: Path):
    store = SqliteMemoryStore(tmp_path / "desktopmate.db")
    store.upsert(make_entry())

    deleted = store.delete(MemoryScope.USER, "user_1", "language")

    assert deleted == 1
    assert store.read_scope(MemoryScope.USER, "user_1") == []
    with sqlite3.connect(tmp_path / "desktopmate.db") as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            "SELECT status FROM memory_items WHERE key = 'language'"
        ).fetchone()
    assert row["status"] == "deleted"


def test_sqlite_memory_read_context_scopes(tmp_path: Path):
    store = SqliteMemoryStore(tmp_path / "desktopmate.db")
    store.upsert(make_entry(scope=MemoryScope.SESSION, subject_id="session_1", key="task", value="改前端"))
    store.upsert(make_entry(scope=MemoryScope.USER, subject_id="user_1", key="language", value="中文"))
    store.upsert(make_entry(scope=MemoryScope.PROJECT, subject_id="proj_1", key="stack", value="Vue"))

    context = store.read_context(session_id="session_1", user_id="user_1", project_id="proj_1")
    keys = {entry.key for entry in context}
    assert keys == {"task", "language", "stack"}

    without_project = store.read_context(session_id="session_1", user_id="user_1")
    assert "stack" not in {entry.key for entry in without_project}


def test_sqlite_memory_stores_sensitivity_and_status(tmp_path: Path):
    store = SqliteMemoryStore(tmp_path / "desktopmate.db")
    store.upsert(
        make_entry(
            key="medical_record",
            value="sensitive info",
            sensitivity=MemorySensitivity.PRIVATE,
            status=MemoryStatus.CANDIDATE,
            confidence=0.6,
        )
    )

    # candidate 不参与 active 上下文
    assert store.read_scope(MemoryScope.USER, "user_1") == []

    with sqlite3.connect(tmp_path / "desktopmate.db") as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute("SELECT * FROM memory_items WHERE key = 'medical_record'").fetchone()
    assert row["sensitivity"] == "private"
    assert row["status"] == "candidate"
    assert row["confidence"] == 0.6


def test_sqlite_memory_writes_event_log(tmp_path: Path):
    store = SqliteMemoryStore(tmp_path / "desktopmate.db")
    store.upsert(make_entry())
    store.delete(MemoryScope.USER, "user_1", "language")

    with sqlite3.connect(tmp_path / "desktopmate.db") as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute("SELECT action FROM memory_events ORDER BY created_at").fetchall()
    assert [row["action"] for row in rows] == ["upsert", "delete"]


def test_sqlite_memory_expired_entry_is_excluded(tmp_path: Path):
    from datetime import UTC, datetime, timedelta

    store = SqliteMemoryStore(tmp_path / "desktopmate.db")
    store.upsert(
        make_entry(
            key="temp",
            value="short-lived",
            expires_at=datetime.now(UTC) - timedelta(seconds=1),
        )
    )

    assert store.read_scope(MemoryScope.USER, "user_1") == []


def test_sqlite_memory_event_jsonl_audit(tmp_path: Path):
    store = SqliteMemoryStore(tmp_path / "desktopmate.db", events_path=tmp_path / "memory_events.jsonl")
    store.upsert(make_entry())

    lines = (tmp_path / "memory_events.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    event = json.loads(lines[0])
    assert event["action"] == "upsert"
