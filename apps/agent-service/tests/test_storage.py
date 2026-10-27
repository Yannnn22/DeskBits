"""S6 存储统计与清理：usage 统计、孤儿附件、trace 过期清理。"""

import asyncio
import os
import time
from pathlib import Path

from fastapi.testclient import TestClient

from desktopmate_agent.attachments.store import AttachmentStore
from desktopmate_agent.config import Settings
from desktopmate_agent.main import create_app
from desktopmate_agent.runtime.events import RuntimeEventBus
from desktopmate_agent.sessions.store import SessionStore
from desktopmate_agent.storage.service import StorageService

AUTH_HEADERS = {"X-DesktopMate-Token": "test-token"}


def make_settings(tmp_path: Path, **overrides) -> Settings:
    defaults = {
        "trace_dir": tmp_path / "traces",
        "settings_dir": tmp_path / "settings",
        "app_data_dir": tmp_path,
        "uploads_dir": tmp_path / "uploads",
        "knowledge_dir": tmp_path / "knowledge",
        "knowledge_db_path": tmp_path / "desktopmate.db",
        "session_db_path": tmp_path / "desktopmate.db",
        "auth_token": "test-token",
        "openai_api_key": None,
        "deepseek_api_key": None,
    }
    defaults.update(overrides)
    return Settings(**defaults)


def make_service(tmp_path: Path, *, bus: RuntimeEventBus | None = None) -> tuple[StorageService, AttachmentStore, Settings]:
    settings = make_settings(tmp_path)
    store = AttachmentStore(settings.uploads_dir)
    service = StorageService(
        settings=settings,
        attachment_store=store,
        runtime_events=bus,
    )
    return service, store, settings


# --- usage 统计 ---


def test_usage_counts_bytes(tmp_path):
    service, _, settings = make_service(tmp_path)
    settings.uploads_dir.mkdir(parents=True, exist_ok=True)
    settings.knowledge_dir.mkdir(parents=True, exist_ok=True)
    settings.trace_dir.mkdir(parents=True, exist_ok=True)
    (settings.uploads_dir / "a.png").write_bytes(b"x" * 100)
    (settings.knowledge_dir / "originals").mkdir(parents=True)
    (settings.knowledge_dir / "originals" / "doc.md").write_bytes(b"y" * 200)
    (settings.trace_dir / "run_1.jsonl").write_bytes(b"z" * 300)
    (settings.knowledge_db_path).write_bytes(b"db" * 10)  # 20 bytes

    usage = service.usage()
    assert usage["uploads_bytes"] == 100
    assert usage["knowledge_bytes"] == 200
    assert usage["traces_bytes"] == 300
    assert usage["database_bytes"] == 20
    assert usage["data_dir"] == str(tmp_path)


# --- 孤儿附件 ---


def test_orphan_upload_detection_and_cleanup(tmp_path):
    service, store, settings = make_service(tmp_path)
    referenced = store.save(filename="keep.png", mime="image/png", content=b"keep")
    orphan = store.save(filename="drop.png", mime="image/png", content=b"drop")

    # 消息 metadata 引用 keep
    session_store = SessionStore(settings.session_db_path)
    session_store.ensure_session("s1", mode="chat")
    session_store.add_message("s1", role="user", text="看图", metadata={"attachments": [referenced.id]})

    orphans = service.orphan_upload_ids()
    assert orphan.id in orphans
    assert referenced.id not in orphans

    result = asyncio.run(service.cleanup(targets=["orphan_uploads"], older_than_days=30))
    assert result["orphan_uploads_removed"] == 1
    assert store.get(orphan.id) is None  # 已删
    assert store.get(referenced.id) is not None  # 仍被引用，保留


def test_orphan_upload_all_referenced_kept(tmp_path):
    service, store, _ = make_service(tmp_path)
    a = store.save(filename="a.png", mime="image/png", content=b"a")
    session_store = SessionStore(service.settings.session_db_path)
    session_store.ensure_session("s2", mode="chat")
    session_store.add_message("s2", role="user", text="x", metadata={"attachments": [a.id]})
    assert service.orphan_upload_ids() == []


# --- traces 清理 ---


def test_cleanup_traces_removes_only_old(tmp_path):
    service, _, settings = make_service(tmp_path)
    settings.trace_dir.mkdir(parents=True)
    old = settings.trace_dir / "run_old.jsonl"
    recent = settings.trace_dir / "run_recent.jsonl"
    old.write_bytes(b"old")
    recent.write_bytes(b"recent")
    past = time.time() - 40 * 86_400
    os.utime(old, (past, past))

    result = asyncio.run(service.cleanup(targets=["traces"], older_than_days=30))
    assert result["traces_removed"] == 1
    assert not old.exists()
    assert recent.exists()


# --- runtime 事件留痕 ---


def test_cleanup_records_runtime_event(tmp_path):
    bus = RuntimeEventBus(path=None)
    service, store, settings = make_service(tmp_path, bus=bus)
    store.save(filename="o.png", mime="image/png", content=b"o")

    asyncio.run(service.cleanup(targets=["orphan_uploads", "traces"], older_than_days=30))
    events, _ = bus.events_since(0)
    assert events[-1]["type"] == "storage_cleanup"
    payload = events[-1]["payload"]
    assert payload["orphan_uploads_removed"] == 1
    assert payload["traces_removed"] == 0


def test_cleanup_rejects_invalid_target(tmp_path):
    service, _, _ = make_service(tmp_path)
    try:
        asyncio.run(service.cleanup(targets=["knowledge"], older_than_days=30))
        assert False, "应拒绝 knowledge 目标"
    except ValueError:
        pass


# --- API ---


def test_storage_api_requires_auth(tmp_path):
    client = TestClient(create_app(settings=make_settings(tmp_path)))
    assert client.get("/api/storage/usage").status_code == 401
    assert client.post("/api/storage/cleanup", json={"targets": ["traces"]}).status_code == 401


def test_storage_usage_endpoint(tmp_path):
    client = TestClient(create_app(settings=make_settings(tmp_path)))
    resp = client.get("/api/storage/usage", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    for field in ("data_dir", "database_bytes", "uploads_bytes", "knowledge_bytes", "traces_bytes"):
        assert field in body


def test_storage_cleanup_endpoint_and_validation(tmp_path):
    app = create_app(settings=make_settings(tmp_path))
    client = TestClient(app)

    bad = client.post(
        "/api/storage/cleanup",
        headers=AUTH_HEADERS,
        json={"targets": ["knowledge"]},
    )
    assert bad.status_code == 422
    assert bad.json()["error"]["code"] == "invalid_target"

    ok = client.post(
        "/api/storage/cleanup",
        headers=AUTH_HEADERS,
        json={"targets": ["traces", "orphan_uploads"], "older_than_days": 7},
    )
    assert ok.status_code == 200
    assert ok.json()["targets"] == ["traces", "orphan_uploads"]
