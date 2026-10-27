from datetime import UTC, datetime, timedelta

from desktopmate_agent.memory.models import MemoryEntry, MemoryScope
from desktopmate_agent.memory.store import JsonlMemoryStore, RedisMemoryStore


class FakeRedisClient:
    def __init__(self) -> None:
        self.lists: dict[str, list[str]] = {}

    def rpush(self, key: str, value: str) -> None:
        self.lists.setdefault(key, []).append(value)

    def lrange(self, key: str, start: int, end: int) -> list[str]:
        values = self.lists.get(key, [])
        if end == -1:
            return values[start:]
        return values[start : end + 1]

    def delete(self, key: str) -> int:
        existed = key in self.lists
        self.lists.pop(key, None)
        return int(existed)


def test_jsonl_memory_store_writes_and_reads_session(tmp_path):
    store = JsonlMemoryStore(tmp_path)
    entry = MemoryEntry(session_id="s1", key="preferred_language", value="zh-CN")

    store.append(entry)

    entries = store.read_session("s1")
    assert len(entries) == 1
    assert entries[0].key == "preferred_language"
    assert entries[0].value == "zh-CN"


def test_jsonl_memory_store_sanitizes_session_id(tmp_path):
    store = JsonlMemoryStore(tmp_path)
    entry = MemoryEntry(session_id="s1/../s2", key="topic", value="rag")

    store.append(entry)

    assert (tmp_path / "session-s1_.._s2.jsonl").exists()
    assert store.read_session("s1/../s2")[0].value == "rag"


def test_jsonl_memory_store_reads_context_across_scopes(tmp_path):
    store = JsonlMemoryStore(tmp_path)
    store.append(MemoryEntry(scope=MemoryScope.USER, subject_id="u1", key="language", value="中文"))
    store.append(MemoryEntry(scope=MemoryScope.PROJECT, subject_id="p1", key="stack", value="Vue"))
    store.append(MemoryEntry(session_id="s1", key="current_task", value="memory"))

    entries = store.read_context(session_id="s1", user_id="u1", project_id="p1")

    assert [(entry.scope, entry.key) for entry in entries] == [
        (MemoryScope.SESSION, "current_task"),
        (MemoryScope.USER, "language"),
        (MemoryScope.PROJECT, "stack"),
    ]


def test_jsonl_memory_store_ignores_expired_memory(tmp_path):
    store = JsonlMemoryStore(tmp_path)
    store.append(
        MemoryEntry(
            session_id="s1",
            key="stale",
            value="old",
            expires_at=datetime.now(UTC) - timedelta(seconds=1),
        )
    )

    assert store.read_context(session_id="s1") == []


def test_jsonl_memory_store_deletes_matching_key_in_scope(tmp_path):
    store = JsonlMemoryStore(tmp_path)
    store.append(MemoryEntry(scope=MemoryScope.USER, subject_id="u1", key="language", value="中文"))
    store.append(MemoryEntry(scope=MemoryScope.USER, subject_id="u1", key="theme", value="dark"))

    deleted_count = store.delete(MemoryScope.USER, "u1", "language")

    entries = store.read_scope(MemoryScope.USER, "u1")
    assert deleted_count == 1
    assert [(entry.key, entry.value) for entry in entries] == [("theme", "dark")]


def test_jsonl_memory_store_upserts_matching_key_in_scope(tmp_path):
    store = JsonlMemoryStore(tmp_path)
    store.upsert(MemoryEntry(scope=MemoryScope.USER, subject_id="u1", key="language", value="中文"))
    store.upsert(MemoryEntry(scope=MemoryScope.USER, subject_id="u1", key="language", value="English"))

    entries = store.read_scope(MemoryScope.USER, "u1")

    assert len(entries) == 1
    assert entries[0].key == "language"
    assert entries[0].value == "English"


def test_redis_memory_store_writes_and_reads_session():
    store = RedisMemoryStore(redis_client=FakeRedisClient())
    entry = MemoryEntry(session_id="s1", key="preferred_language", value="zh-CN")

    store.append(entry)

    entries = store.read_session("s1")
    assert len(entries) == 1
    assert entries[0].key == "preferred_language"
    assert entries[0].value == "zh-CN"


def test_redis_memory_store_upserts_matching_key_in_scope():
    store = RedisMemoryStore(redis_client=FakeRedisClient())
    store.upsert(MemoryEntry(scope=MemoryScope.USER, subject_id="u1", key="language", value="中文"))
    store.upsert(MemoryEntry(scope=MemoryScope.USER, subject_id="u1", key="language", value="English"))

    entries = store.read_scope(MemoryScope.USER, "u1")

    assert len(entries) == 1
    assert entries[0].key == "language"
    assert entries[0].value == "English"


def test_redis_memory_store_reads_context_and_ignores_expired_memory():
    store = RedisMemoryStore(redis_client=FakeRedisClient())
    store.append(MemoryEntry(scope=MemoryScope.USER, subject_id="u1", key="language", value="中文"))
    store.append(MemoryEntry(session_id="s1", key="current_task", value="memory"))
    store.append(
        MemoryEntry(
            scope=MemoryScope.PROJECT,
            subject_id="p1",
            key="stale",
            value="old",
            expires_at=datetime.now(UTC) - timedelta(seconds=1),
        )
    )

    entries = store.read_context(session_id="s1", user_id="u1", project_id="p1")

    assert [(entry.scope, entry.key) for entry in entries] == [
        (MemoryScope.SESSION, "current_task"),
        (MemoryScope.USER, "language"),
    ]


def test_redis_memory_store_deletes_matching_key_in_scope():
    store = RedisMemoryStore(redis_client=FakeRedisClient())
    store.append(MemoryEntry(scope=MemoryScope.USER, subject_id="u1", key="language", value="中文"))
    store.append(MemoryEntry(scope=MemoryScope.USER, subject_id="u1", key="theme", value="dark"))

    deleted_count = store.delete(MemoryScope.USER, "u1", "language")

    entries = store.read_scope(MemoryScope.USER, "u1")
    assert deleted_count == 1
    assert [(entry.key, entry.value) for entry in entries] == [("theme", "dark")]
