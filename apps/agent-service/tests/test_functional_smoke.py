from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from desktopmate_agent.config import Settings
from desktopmate_agent.main import create_app


AUTH_HEADERS = {"X-DesktopMate-Token": "test-token"}
WS_AUTH = "?token=test-token"


def make_settings(tmp_path: Path, tmp_allowed_root: Path) -> Settings:
    return Settings(
        trace_dir=tmp_path / "traces",
        draft_dir=tmp_path / "drafts",
        memory_dir=tmp_path / "memory",
        knowledge_dir=tmp_path / "knowledge",
        allowed_roots=str(tmp_allowed_root),
        auth_token="test-token",
        openai_api_key=None,
        deepseek_api_key=None,
    )


@pytest.mark.smoke
def test_health_is_ready(tmp_path, tmp_allowed_root):
    client = TestClient(create_app(settings=make_settings(tmp_path, tmp_allowed_root)))

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ready"
    assert response.json()["service"] == "desktopmate-agent-service"


@pytest.mark.smoke
def test_http_chat_search_and_read_tools(tmp_path, tmp_allowed_root):
    note = tmp_allowed_root / "note.md"
    note.write_text("# DesktopMate\n", encoding="utf-8")
    client = TestClient(create_app(settings=make_settings(tmp_path, tmp_allowed_root)))

    chat = client.post("/api/chat/invoke", json={"text": "hello"}, headers=AUTH_HEADERS).json()
    search = client.post(
        "/api/chat/invoke", json={"text": "/search note", "mode": "agent"}, headers=AUTH_HEADERS
    ).json()
    read = client.post(
        "/api/chat/invoke", json={"text": f"/read {note}", "mode": "agent"}, headers=AUTH_HEADERS
    ).json()

    assert chat["text"] == "收到。"
    assert search["text"] == "找到 1 个文件"
    assert read["text"] == "读取文件成功"


@pytest.mark.smoke
def test_http_chat_denies_tool_that_needs_interactive_approval(tmp_path, tmp_allowed_root):
    client = TestClient(create_app(settings=make_settings(tmp_path, tmp_allowed_root)))

    response = client.post(
        "/api/chat/invoke",
        json={"text": f"/draft {tmp_allowed_root / 'proposal.md'}\n# Proposal", "mode": "agent"},
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 200
    body = response.json()
    assert "已取消执行 draft_write" in body["text"]
    assert "桌面聊天窗口" in body["text"]
    # D5: HTTP 无法弹确认框 → 响应标记 requires_websocket，客户端可据此提示用户
    assert body["requires_websocket"] is True


@pytest.mark.smoke
def test_knowledge_search_and_refresh_pick_up_new_document(tmp_path, tmp_allowed_root):
    client = TestClient(create_app(settings=make_settings(tmp_path, tmp_allowed_root)))
    empty = client.post(
        "/api/chat/invoke", json={"text": "/knowledge Milvus"}, headers=AUTH_HEADERS
    ).json()
    assert empty["text"]  # 模型基于检索结果的综合回答（空索引）
    empty_events = client.get(
        f"/api/runs/{empty['run_id']}/trace", headers=AUTH_HEADERS
    ).json()["events"]
    empty_tool = next(
        event for event in empty_events if event["type"] == "tool_call_finished"
    )
    assert empty_tool["payload"]["result"]["data"]["matches"] == []

    # S7：统一摄入源为 SQLite——上传走 API（写入 document_chunks + 触发索引刷新）
    uploaded = client.post(
        "/api/knowledge/documents",
        headers=AUTH_HEADERS,
        files={
            "file": (
                "milvus.md",
                "# Milvus\n\nMilvus 需要先启动 Docker 服务并配置 collection。".encode("utf-8"),
                "text/markdown",
            )
        },
    )
    assert uploaded.status_code == 200, uploaded.text

    refreshed = client.post(
        "/api/chat/invoke",
        json={"text": "/knowledge-refresh", "mode": "agent"},
        headers=AUTH_HEADERS,
    ).json()
    found = client.post(
        "/api/chat/invoke", json={"text": "/knowledge Milvus"}, headers=AUTH_HEADERS
    ).json()

    assert refreshed["text"] == "知识库索引已刷新，当前 1 个片段"
    assert found["text"]  # 模型基于检索结果的综合回答
    found_events = client.get(
        f"/api/runs/{found['run_id']}/trace", headers=AUTH_HEADERS
    ).json()["events"]
    found_tool = next(
        event for event in found_events if event["type"] == "tool_call_finished"
    )
    assert len(found_tool["payload"]["result"]["data"]["matches"]) == 1


@pytest.mark.smoke
def test_memory_remember_list_and_forget_lifecycle(tmp_path, tmp_allowed_root):
    client = TestClient(create_app(settings=make_settings(tmp_path, tmp_allowed_root)))
    session_id = "smoke_session"

    remembered = client.post(
        "/api/chat/invoke",
        json={"text": "/remember preferred_language 中文", "session_id": session_id},
        headers=AUTH_HEADERS,
    ).json()
    listed = client.post(
        "/api/chat/invoke",
        json={"text": "/memories", "session_id": session_id},
        headers=AUTH_HEADERS,
    ).json()
    forgotten = client.post(
        "/api/chat/invoke",
        json={"text": "/forget user preferred_language", "session_id": session_id},
        headers=AUTH_HEADERS,
    ).json()
    empty = client.post(
        "/api/chat/invoke",
        json={"text": "/memories", "session_id": session_id},
        headers=AUTH_HEADERS,
    ).json()

    assert remembered["text"] == "已记住 preferred_language。"
    assert "- user.preferred_language: 中文" in listed["text"]
    assert forgotten["text"] == "已删除 1 条记忆。"
    assert empty["text"] == "当前没有可用记忆。"


@pytest.mark.smoke
def test_websocket_streams_chat_and_tool_events(tmp_path, tmp_allowed_root):
    note = tmp_allowed_root / "note.md"
    note.write_text("# DesktopMate\n", encoding="utf-8")
    client = TestClient(create_app(settings=make_settings(tmp_path, tmp_allowed_root)))

    with client.websocket_connect(f"/ws/chat{WS_AUTH}") as websocket:
        websocket.send_json({"type": "user_message", "text": "hello"})
        chat_events = [websocket.receive_json() for _ in range(3)]

        websocket.send_json({"type": "user_message", "text": "/search note", "mode": "agent"})
        tool_events = [websocket.receive_json() for _ in range(5)]

    assert [event["type"] for event in chat_events] == [
        "agent_started",
        "agent_message",
        "done",
    ]
    assert [event["type"] for event in tool_events] == [
        "agent_started",
        "tool_call_started",
        "tool_call_finished",
        "agent_message",
        "done",
    ]


@pytest.mark.smoke
def test_websocket_permission_approval_creates_draft(tmp_path, tmp_allowed_root):
    target = tmp_allowed_root / "note.md"
    client = TestClient(create_app(settings=make_settings(tmp_path, tmp_allowed_root)))

    with client.websocket_connect(f"/ws/chat{WS_AUTH}") as websocket:
        websocket.send_json(
            {"type": "user_message", "text": f"/draft {target}\n# Draft note\n", "mode": "agent"}
        )
        first = websocket.receive_json()
        permission_event = websocket.receive_json()
        websocket.send_json(
            {
                "type": "permission_decision",
                "request_id": permission_event["payload"]["request_id"],
                "decision": "approved",
            }
        )
        rest = [websocket.receive_json() for _ in range(4)]

    events = [first, permission_event, *rest]
    assert [event["type"] for event in events] == [
        "agent_started",
        "permission_request",
        "tool_call_started",
        "tool_call_finished",
        "agent_message",
        "done",
    ]
    draft_path = Path(events[3]["payload"]["result"]["data"]["draft_path"])
    assert target.exists() is False
    assert draft_path.exists()
