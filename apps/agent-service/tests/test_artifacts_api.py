from pathlib import Path

from fastapi.testclient import TestClient

from desktopmate_agent.config import Settings
from desktopmate_agent.main import create_app


AUTH_HEADERS = {"X-DesktopMate-Token": "test-token"}


def make_settings(tmp_path: Path) -> Settings:
    return Settings(
        auth_token="test-token",
        app_data_dir=tmp_path,
        trace_dir=tmp_path / "traces",
        knowledge_dir=tmp_path / "knowledge",
        knowledge_db_path=tmp_path / "desktopmate.db",
        session_db_path=tmp_path / "desktopmate.db",
        settings_dir=tmp_path / "settings",
        skills_dir=tmp_path / "skills",
        uploads_dir=tmp_path / "uploads",
        openai_api_key=None,
        deepseek_api_key=None,
    )


def test_artifact_requires_confirmation_before_workspace_listing(tmp_path: Path):
    client = TestClient(create_app(settings=make_settings(tmp_path)))

    created = client.post(
        "/api/artifacts",
        headers=AUTH_HEADERS,
        json={
            "kind": "document",
            "title": "项目计划",
            "content": "第一版正文",
            "source_session_id": "session-1",
        },
    )

    assert created.status_code == 200
    artifact_id = created.json()["id"]
    assert created.json()["state"] == "draft"
    assert client.get("/api/artifacts", headers=AUTH_HEADERS).json()["artifacts"] == []

    confirmed = client.post(f"/api/artifacts/{artifact_id}/confirm", headers=AUTH_HEADERS)

    assert confirmed.status_code == 200
    assert confirmed.json()["state"] == "saved"
    assert confirmed.json()["content"] == "第一版正文"
    listed = client.get("/api/artifacts", headers=AUTH_HEADERS).json()["artifacts"]
    assert [item["id"] for item in listed] == [artifact_id]


def test_artifact_revision_keeps_confirmed_version_until_confirmed(tmp_path: Path):
    client = TestClient(create_app(settings=make_settings(tmp_path)))
    artifact = client.post(
        "/api/artifacts",
        headers=AUTH_HEADERS,
        json={"kind": "document", "title": "周报", "content": "旧版本"},
    ).json()
    artifact_id = artifact["id"]
    client.post(f"/api/artifacts/{artifact_id}/confirm", headers=AUTH_HEADERS)

    revision = client.post(
        f"/api/artifacts/{artifact_id}/revisions",
        headers=AUTH_HEADERS,
        json={"content": "候选新版本"},
    )

    assert revision.status_code == 200
    assert revision.json()["content"] == "候选新版本"
    assert revision.json()["pending_version"] == 2
    workspace_read = client.get(f"/api/artifacts/{artifact_id}", headers=AUTH_HEADERS).json()
    assert workspace_read["content"] == "旧版本"
    assert workspace_read["version_state"] == "saved"
    confirmed = client.post(f"/api/artifacts/{artifact_id}/confirm", headers=AUTH_HEADERS)
    assert confirmed.json()["content"] == "候选新版本"
    assert confirmed.json()["current_version"] == 2


def test_only_latest_draft_version_is_pending_before_confirmation(tmp_path: Path):
    client = TestClient(create_app(settings=make_settings(tmp_path)))
    artifact = client.post(
        "/api/artifacts",
        headers=AUTH_HEADERS,
        json={"kind": "document", "title": "提案", "content": "初稿"},
    ).json()
    artifact_id = artifact["id"]

    first_revision = client.post(
        f"/api/artifacts/{artifact_id}/revisions",
        headers=AUTH_HEADERS,
        json={"content": "候选一"},
    ).json()
    second_revision = client.post(
        f"/api/artifacts/{artifact_id}/revisions",
        headers=AUTH_HEADERS,
        json={"content": "候选二"},
    ).json()

    assert first_revision["pending_version"] == 2
    assert second_revision["pending_version"] == 3
    confirmed = client.post(f"/api/artifacts/{artifact_id}/confirm", headers=AUTH_HEADERS).json()
    assert confirmed["content"] == "候选二"
    assert confirmed["current_version"] == 3
    assert confirmed["pending_version"] is None


def test_chat_writing_command_emits_candidate_artifact(tmp_path: Path):
    client = TestClient(create_app(settings=make_settings(tmp_path)))

    with client.websocket_connect("/ws/chat?token=test-token") as websocket:
        websocket.send_json({"type": "user_message", "text": "写一篇项目周报", "mode": "agent"})
        events = []
        while True:
            event = websocket.receive_json()
            events.append(event)
            if event["type"] == "done":
                break

    finished = next(event for event in events if event["type"] == "tool_call_finished")
    artifact = finished["payload"]["result"]["data"]["artifact"]
    assert artifact["title"] == "项目周报"
    assert artifact["state"] == "draft"
    assert artifact["source_session_id"]
    assert client.get("/api/artifacts", headers=AUTH_HEADERS).json()["artifacts"] == []
