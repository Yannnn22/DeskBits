"""真实会话级回归：自然语言输入贯穿 RAG、记忆、待办和受保护文件工具。"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from desktopmate_agent.config import Settings
from desktopmate_agent.main import create_app


AUTH_TOKEN = "conversation-flow-token"
AUTH_HEADERS = {"X-DesktopMate-Token": AUTH_TOKEN}


def make_settings(tmp_path: Path, allowed_root: Path) -> Settings:
    return Settings(
        app_data_dir=tmp_path / "data",
        trace_dir=tmp_path / "traces",
        draft_dir=tmp_path / "drafts",
        knowledge_dir=tmp_path / "knowledge",
        knowledge_db_path=tmp_path / "desktopmate.db",
        session_db_path=tmp_path / "desktopmate.db",
        memory_dir=tmp_path / "memory",
        allowed_roots=str(allowed_root),
        embedding_provider="fake",
        embedding_dimensions=16,
        intent_router="heuristic",
        auth_token=AUTH_TOKEN,
        openai_api_key=None,
        deepseek_api_key=None,
    )


def receive_message(client: TestClient, *, text: str, session_id: str) -> list[dict]:
    events: list[dict] = []
    with client.websocket_connect(f"/ws/chat?token={AUTH_TOKEN}") as websocket:
        websocket.send_json(
            {
                "type": "user_message",
                "session_id": session_id,
                "mode": "agent",
                "text": text,
                "attachments": [],
            }
        )
        while True:
            event = websocket.receive_json()
            events.append(event)
            if event["type"] == "permission_request":
                websocket.send_json(
                    {
                        "type": "permission_decision",
                        "request_id": event["payload"]["request_id"],
                        "decision": "approved",
                    }
                )
            if event["type"] == "done":
                return events


def event_of(events: list[dict], event_type: str) -> dict:
    return next(event for event in events if event["type"] == event_type)


def test_one_conversation_keeps_rag_memory_task_and_tool_state(tmp_path: Path) -> None:
    allowed_root = tmp_path / "allowed"
    allowed_root.mkdir()
    target = allowed_root / "meeting.md"
    target.write_text("旧会议记录", encoding="utf-8")

    app = create_app(settings=make_settings(tmp_path, allowed_root))
    client = TestClient(app)

    uploaded = client.post(
        "/api/knowledge/documents",
        files={
            "file": (
                "personal-notes.md",
                "项目代号是晨星，发布窗口是周五晚上。".encode("utf-8"),
                "text/markdown",
            )
        },
        headers=AUTH_HEADERS,
    )
    assert uploaded.status_code == 200
    session_id = "real-flow-session"

    rag_events = receive_message(
        client,
        text="我的知识库里项目代号是什么？",
        session_id=session_id,
    )
    rag_finished = event_of(rag_events, "tool_call_finished")
    assert rag_finished["payload"]["tool_name"] == "knowledge_search"
    rag_result = rag_finished["payload"]["result"]
    assert any("晨星" in item["text"] for item in rag_result["data"]["matches"])
    rag_answer = event_of(rag_events, "agent_message")["payload"]
    assert rag_answer["citations"]

    memory_events = receive_message(
        client,
        text="/remember session current_project 我负责晨星项目的发布",
        session_id=session_id,
    )
    assert "已记住 current_project" in event_of(memory_events, "agent_message")["payload"]["text"]

    context_events = receive_message(
        client,
        text="刚才我负责什么项目？",
        session_id=session_id,
    )
    memory_context = event_of(context_events, "agent_message")["payload"]["memory_context"]
    assert any(item["value"] == "我负责晨星项目的发布" for item in memory_context)

    task_events = receive_message(
        client,
        text="提醒我明天下午一点吃药",
        session_id=session_id,
    )
    task_finished = event_of(task_events, "tool_call_finished")
    assert task_finished["payload"]["tool_name"] == "task_draft"
    task_candidate = task_finished["payload"]["result"]["data"]
    assert task_candidate["task"]["title"] == "吃药"
    assert client.get("/api/tasks", headers=AUTH_HEADERS).json()["tasks"] == []

    task = task_candidate["task"]
    confirmed = client.post(
        "/api/tasks/confirm-draft",
        headers=AUTH_HEADERS,
        json={
            "session_id": session_id,
            "title": task["title"],
            "description": task["description"],
            "due_at": task["due_at"],
            "timezone": "Asia/Shanghai",
            "create_reminder": True,
            "category": task["category"],
            "priority": task["priority"],
            "next_action": task["next_action"],
            "reminder_message": "下午一点该吃药了",
            "memory_summary": "已安排明天下午一点吃药。",
        },
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["task"]["title"] == "吃药"
    assert confirmed.json()["memory"]["key"].startswith("task:")
    assert len(client.get("/api/tasks", headers=AUTH_HEADERS).json()["tasks"]) == 1

    assert target.read_text(encoding="utf-8") == "旧会议记录"
    patch_events = receive_message(
        client,
        text=f"/patch {target}\n新的会议记录",
        session_id=session_id,
    )
    permission = event_of(patch_events, "permission_request")["payload"]
    assert permission["tool_name"] == "file_patch"
    assert permission["review"]["changed"] is True, permission
    assert "新的会议记录" in permission["review"]["diff"], permission
    assert target.read_text(encoding="utf-8") == "新的会议记录"

    messages = client.get(
        f"/api/sessions/{session_id}/messages",
        headers=AUTH_HEADERS,
    ).json()["messages"]
    assert len(messages) == 10
    assistant_metadata = [
        item["metadata"]
        for item in messages
        if item["role"] == "assistant" and item["metadata"]
    ]
    tool_names = {
        call["name"]
        for metadata in assistant_metadata
        for call in metadata.get("tool_calls", [])
    }
    assert {"knowledge_search", "task_draft", "file_patch"} <= tool_names

    trace = client.get(
        f"/api/runs/{patch_events[0]['run_id']}/trace",
        headers=AUTH_HEADERS,
    ).json()["events"]
    assert any(event["type"] == "permission_request" for event in trace)
    json.dumps(trace, ensure_ascii=False)
