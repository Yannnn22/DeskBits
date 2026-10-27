"""S2 Runtime Event Bus：事件总线、状态快照、API 与 WebSocket 订阅。"""

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from desktopmate_agent.config import Settings
from desktopmate_agent.main import create_app
from desktopmate_agent.runtime.events import RuntimeEventBus, event_id

AUTH_TOKEN = "test-token"
AUTH_HEADERS = {"X-DesktopMate-Token": AUTH_TOKEN}


def make_settings(tmp_path: Path, **overrides) -> Settings:
    defaults = {
        "trace_dir": tmp_path / "traces",
        "settings_dir": tmp_path / "settings",
        "auth_token": AUTH_TOKEN,
        "openai_api_key": None,
        "deepseek_api_key": None,
    }
    defaults.update(overrides)
    return Settings(**defaults)


# --- 总线基本行为 ---


async def test_append_and_events_since_cursor():
    bus = RuntimeEventBus(path=None)
    await bus.append("run_started", {"run_id": "r1"})
    await bus.append("run_completed", {"run_id": "r1"})

    events, latest = bus.events_since(0)
    assert [e["type"] for e in events] == ["run_started", "run_completed"]
    assert [e["id"] for e in events] == ["evt_00000001", "evt_00000002"]
    assert latest == 2

    tail, latest2 = bus.events_since(1)
    assert [e["type"] for e in tail] == ["run_completed"]
    assert latest2 == 2

    empty, latest3 = bus.events_since(2)
    assert empty == []
    assert latest3 == 2


async def test_ring_buffer_keeps_last_1000():
    bus = RuntimeEventBus(path=None, max_events=1000)
    for i in range(1005):
        await bus.append("run_progress", {"n": i})

    assert bus.event_count() == 1000
    events, _ = bus.events_since(0, limit=1000)
    assert len(events) == 1000
    assert events[0]["id"] == event_id(6)  # 最旧的 5 条被丢弃，seq 仍单调


async def test_snapshot_tracks_status_and_message():
    bus = RuntimeEventBus()
    assert bus.snapshot()["last_run_status"] is None

    await bus.append("run_started", {"run_id": "r1"})
    assert bus.snapshot()["last_run_status"] == "running"

    await bus.append("run_progress", {"run_id": "r1", "stage": "tool", "tool": "knowledge_search"})
    assert bus.snapshot()["last_message"] == "正在执行 knowledge_search"

    await bus.append("run_progress", {"run_id": "r1", "stage": "answer", "text": "答案来了"})
    assert bus.snapshot()["last_message"] == "答案来了"

    await bus.append("run_completed", {"run_id": "r1"})
    assert bus.snapshot()["last_run_status"] == "completed"

    await bus.append("run_failed", {"run_id": "r2", "reason": "boom"})
    assert bus.snapshot()["last_run_status"] == "failed"

    snap = bus.snapshot(active_runs=2, pending_permissions=1, due_reminders=3)
    assert snap["active_runs"] == 2
    assert snap["pending_permissions"] == 1
    assert snap["due_reminders"] == 3


async def test_subscribe_unsubscribe_and_fanout():
    bus = RuntimeEventBus(path=None)
    received: list[str] = []

    async def subscriber(event: dict) -> None:
        received.append(event["type"])

    sub_id = bus.subscribe(subscriber)
    await bus.append("run_started", {})
    await bus.append("run_completed", {})
    assert received == ["run_started", "run_completed"]

    assert bus.unsubscribe(sub_id) is True
    await bus.append("run_failed", {})
    assert received == ["run_started", "run_completed"]


async def test_broken_subscriber_is_removed():
    bus = RuntimeEventBus(path=None)

    async def broken(event: dict) -> None:
        raise RuntimeError("socket closed")

    bus.subscribe(broken)
    await bus.append("run_started", {})
    assert bus.subscriber_count() == 0


async def test_slow_subscriber_is_removed_after_timeout():
    """B3 修复：慢/卡住的订阅者不应阻塞后续事件，超时后被移除。"""
    bus = RuntimeEventBus(path=None)

    async def slow(event: dict) -> None:
        await asyncio.sleep(30)  # 远超 5s 推送超时

    async def fast(event: dict) -> None:
        pass

    bus.subscribe(slow)
    bus.subscribe(fast)
    # 慢订阅者被 wait_for(5s) 掐掉，正常订阅者收到事件
    await bus.append("run_started", {})
    assert bus.subscriber_count() == 1  # slow 被移除，fast 保留
    # 后续事件不再被慢订阅者拖累
    await bus.append("run_completed", {})
    assert bus.subscriber_count() == 1


async def test_persistence_reload_and_seq_continues(tmp_path):
    path = tmp_path / "runtime_events.jsonl"
    bus = RuntimeEventBus(path)
    await bus.append("run_started", {"run_id": "r1"})
    await bus.append("run_completed", {"run_id": "r1"})

    reloaded = RuntimeEventBus(path)
    events, _ = reloaded.events_since(0)
    assert [e["type"] for e in events] == ["run_started", "run_completed"]

    await reloaded.append("run_started", {"run_id": "r2"})
    events, _ = reloaded.events_since(0)
    assert events[-1]["id"] == "evt_00000003"  # seq 不重复
    assert reloaded.snapshot()["last_run_status"] == "running"


# --- HTTP API ---


def test_runtime_status_requires_auth(tmp_path):
    client = TestClient(create_app(settings=make_settings(tmp_path)))
    assert client.get("/api/runtime/status").status_code == 401
    assert client.get("/api/runtime/events").status_code == 401


def test_runtime_status_shape(tmp_path):
    client = TestClient(create_app(settings=make_settings(tmp_path)))
    data = client.get("/api/runtime/status", headers=AUTH_HEADERS).json()
    assert set(data) == {
        "active_runs",
        "last_run_status",
        "last_message",
        "pending_permissions",
        "due_reminders",
    }
    assert data["active_runs"] == 0
    assert data["pending_permissions"] == 0
    assert data["due_reminders"] == 0


def test_runtime_events_endpoint_pagination(tmp_path):
    app = create_app(settings=make_settings(tmp_path))
    bus = app.state.runtime_bus
    for i in range(3):
        asyncio.run(bus.append("run_progress", {"n": i}))
    client = TestClient(app)

    data = client.get("/api/runtime/events", headers=AUTH_HEADERS).json()
    assert len(data["events"]) == 3
    assert data["events"][0]["id"] == "evt_00000001"
    assert data["next_cursor"] == "evt_00000003"

    # 用 evt_ id 作为 since cursor 增量拉取
    data2 = client.get(
        f"/api/runtime/events?since={data['events'][1]['id']}",
        headers=AUTH_HEADERS,
    ).json()
    assert len(data2["events"]) == 1
    assert data2["events"][0]["id"] == "evt_00000003"

    # 纯数字 cursor 也支持
    data3 = client.get("/api/runtime/events?since=3", headers=AUTH_HEADERS).json()
    assert data3["events"] == []


# --- 集成：chat run 发布事件 ---


def test_chat_run_publishes_runtime_events(tmp_path):
    settings = make_settings(tmp_path)
    app = create_app(settings=settings)
    bus = app.state.runtime_bus
    client = TestClient(app)

    response = client.post("/api/chat/invoke", headers=AUTH_HEADERS, json={"text": "hello"})
    assert response.status_code == 200

    events, _ = bus.events_since(0)
    types = [e["type"] for e in events]
    assert types[0] == "run_started"
    assert types[-1] == "run_completed"
    assert "run_progress" in types
    assert bus.snapshot()["last_run_status"] == "completed"
    assert bus.snapshot()["last_message"]  # agent_message 文本被跟踪


# --- WebSocket /ws/runtime ---


def test_ws_runtime_rejects_bad_token(tmp_path):
    client = TestClient(create_app(settings=make_settings(tmp_path)))
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/ws/runtime?token=wrong"):
            pass
    assert exc_info.value.code == 1008


def test_ws_runtime_receives_snapshot_and_run_events(tmp_path):
    settings = make_settings(tmp_path)
    app = create_app(settings=settings)
    client = TestClient(app)

    with client.websocket_connect(f"/ws/runtime?token={AUTH_TOKEN}") as runtime_ws:
        first = runtime_ws.receive_json()
        assert first["type"] == "runtime_status"

        # 同步触发一次 chat run
        response = client.post("/api/chat/invoke", headers=AUTH_HEADERS, json={"text": "hi"})
        assert response.status_code == 200

        types: list[str] = []
        while "run_completed" not in types:
            event = runtime_ws.receive_json()
            types.append(event["type"])

        assert types[0] == "run_started"
        assert "run_completed" in types
