from pathlib import Path

from fastapi.testclient import TestClient

from desktopmate_agent.config import Settings
from desktopmate_agent.main import create_app

AUTH_TOKEN = "test-token"
AUTH_HEADERS = {"X-DesktopMate-Token": AUTH_TOKEN}


def make_settings(tmp_path: Path) -> Settings:
    return Settings(
        trace_dir=tmp_path / "traces",
        auth_token=AUTH_TOKEN,
        openai_api_key=None,
        deepseek_api_key=None,
    )


def run_invoke(client: TestClient, text: str = "hello") -> tuple[str, str]:
    response = client.post(
        "/api/chat/invoke",
        json={"text": text},
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 200
    data = response.json()
    return data["session_id"], data["run_id"]


def test_run_trace_lists_public_events(tmp_path):
    client = TestClient(create_app(settings=make_settings(tmp_path)))
    session_id, run_id = run_invoke(client)

    response = client.get(f"/api/runs/{run_id}/trace", headers=AUTH_HEADERS)

    assert response.status_code == 200
    events = response.json()["events"]
    types = [event["type"] for event in events]
    # intent_routed 是意图路由埋点事件（2026-08-13 起），出现在每次 run 的 trace 中。
    assert types == ["agent_started", "intent_routed", "agent_message", "done"]
    assert all(event["run_id"] == run_id for event in events)
    assert all(event["session_id"] == session_id for event in events)
    assert all(event["id"].startswith("event_") for event in events)
    assert all(event["created_at"] for event in events)


def test_run_trace_missing_404(tmp_path):
    client = TestClient(create_app(settings=make_settings(tmp_path)))

    response = client.get("/api/runs/run_missing/trace", headers=AUTH_HEADERS)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "run_not_found"


def test_session_runs_lists_runs(tmp_path):
    client = TestClient(create_app(settings=make_settings(tmp_path)))
    session_id, run_id = run_invoke(client)
    second = client.post(
        "/api/chat/invoke",
        json={"text": "hello again", "session_id": session_id},
        headers=AUTH_HEADERS,
    ).json()

    response = client.get(f"/api/sessions/{session_id}/runs", headers=AUTH_HEADERS)

    assert response.status_code == 200
    runs = response.json()["runs"]
    assert run_id in runs
    assert second["run_id"] in runs


def test_query_events_filters_by_session_and_type(tmp_path):
    client = TestClient(create_app(settings=make_settings(tmp_path)))
    session_id, run_id = run_invoke(client)

    by_session = client.get(
        f"/api/events?session_id={session_id}",
        headers=AUTH_HEADERS,
    ).json()["events"]
    by_type = client.get(
        "/api/events?type=done",
        headers=AUTH_HEADERS,
    ).json()["events"]
    by_run = client.get(
        f"/api/events?run_id={run_id}&type=agent_started",
        headers=AUTH_HEADERS,
    ).json()["events"]

    assert len(by_session) == 4  # agent_started + intent_routed + agent_message + done
    assert all(event["session_id"] == session_id for event in by_session)
    assert len(by_type) == 1 and by_type[0]["type"] == "done"
    assert len(by_run) == 1 and by_run[0]["type"] == "agent_started"


def test_prune_traces_returns_count(tmp_path):
    client = TestClient(create_app(settings=make_settings(tmp_path)))
    run_invoke(client)

    response = client.delete("/api/traces", headers=AUTH_HEADERS)

    assert response.status_code == 200
    assert response.json()["removed"] == 0  # retention 默认 7 天，没有过期文件


def test_trace_apis_require_auth(tmp_path):
    client = TestClient(create_app(settings=make_settings(tmp_path)))

    assert client.get("/api/events").status_code == 401
    assert client.get("/api/runs/run_x/trace").status_code == 401
    assert client.get("/api/sessions/session_x/runs").status_code == 401


def test_intent_routing_is_traced_with_source_and_confidence(tmp_path):
    """意图路由埋点：intent_routed 事件记录来源(heuristic/model)、置信度与意图类型。"""
    from desktopmate_agent.trace.store import JsonlTraceStore

    client = TestClient(create_app(settings=make_settings(tmp_path)))
    session_id, run_id = run_invoke(client, text="你好")

    trace_store = JsonlTraceStore(make_settings(tmp_path).trace_dir)
    events = trace_store.read_run(run_id)
    routed = [e for e in events if e.type == "intent_routed"]

    assert len(routed) >= 1
    first = routed[0].payload
    assert first["intent_type"] in {"direct_response", "tool_call", "refusal"}
    assert first["source"] in {"heuristic", "model", "react_plan", "react_task", "fallback"}
    assert 0.0 <= first["confidence"] <= 1.0
