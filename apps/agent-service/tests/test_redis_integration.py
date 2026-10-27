import os
from datetime import UTC, datetime, timedelta

import pytest
from redis import Redis

from desktopmate_agent.memory.models import MemoryEntry, MemoryScope
from desktopmate_agent.memory.store import RedisMemoryStore


TEST_REDIS_URL = os.getenv(
    "DESKTOPMATE_TEST_REDIS_URL",
    "redis://127.0.0.1:16379/15",
)


@pytest.fixture
def redis_client() -> Redis:
    client = Redis.from_url(TEST_REDIS_URL, decode_responses=True)
    try:
        client.ping()
    except Exception as exc:
        pytest.skip(f"Redis is not reachable at {TEST_REDIS_URL}: {exc}")
    client.flushdb()
    yield client
    client.flushdb()
    client.close()


@pytest.mark.integration
def test_redis_memory_store_reads_context_from_real_redis(redis_client: Redis):
    store = RedisMemoryStore(
        redis_client=redis_client,
        key_prefix="desktopmate:test:memory",
    )
    store.append(MemoryEntry(scope=MemoryScope.USER, subject_id="u1", key="language", value="中文"))
    store.append(MemoryEntry(session_id="s1", key="current_task", value="Redis 联调"))

    entries = store.read_context(session_id="s1", user_id="u1")

    assert [(entry.scope, entry.key) for entry in entries] == [
        (MemoryScope.SESSION, "current_task"),
        (MemoryScope.USER, "language"),
    ]


@pytest.mark.integration
def test_redis_memory_store_upserts_and_deletes_on_real_redis(redis_client: Redis):
    store = RedisMemoryStore(
        redis_client=redis_client,
        key_prefix="desktopmate:test:memory",
    )
    store.upsert(MemoryEntry(scope=MemoryScope.USER, subject_id="u1", key="language", value="中文"))
    store.upsert(MemoryEntry(scope=MemoryScope.USER, subject_id="u1", key="language", value="English"))

    entries = store.read_scope(MemoryScope.USER, "u1")
    assert len(entries) == 1
    assert entries[0].value == "English"

    deleted = store.delete(MemoryScope.USER, "u1", "language")

    assert deleted == 1
    assert store.read_scope(MemoryScope.USER, "u1") == []


@pytest.mark.integration
def test_redis_memory_store_filters_expired_entries_on_real_redis(redis_client: Redis):
    store = RedisMemoryStore(
        redis_client=redis_client,
        key_prefix="desktopmate:test:memory",
    )
    store.append(
        MemoryEntry(
            session_id="s1",
            key="stale",
            value="old",
            expires_at=datetime.now(UTC) - timedelta(seconds=1),
        )
    )

    entries = store.read_context(session_id="s1")

    assert entries == []
