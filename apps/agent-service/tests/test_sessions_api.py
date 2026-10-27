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


def create_session(client: TestClient, title: str = "New Conversation") -> dict:
    response = client.post("/api/sessions", json={"title": title}, headers=AUTH_HEADERS)
    assert response.status_code == 200
    return response.json()


def test_create_session_returns_expected_shape(tmp_path):
    client = TestClient(create_app(settings=make_settings(tmp_path)))

    session = create_session(client, title="My Session")

    assert session["id"].startswith("session_")
    assert session["title"] == "My Session"
    assert session["mode"] == "agent"
    assert session["archived"] is False
    assert session["created_at"]
    assert session["updated_at"]


def test_sessions_require_auth(tmp_path):
    client = TestClient(create_app(settings=make_settings(tmp_path)))

    response = client.get("/api/sessions")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


def test_list_sessions_scopes(tmp_path):
    client = TestClient(create_app(settings=make_settings(tmp_path)))
    first = create_session(client, title="First")
    second = create_session(client, title="Second")
    client.patch(f"/api/sessions/{second['id']}", json={"archived": True}, headers=AUTH_HEADERS)

    recent = client.get("/api/sessions?scope=recent", headers=AUTH_HEADERS).json()["sessions"]
    archived = client.get("/api/sessions?scope=archived", headers=AUTH_HEADERS).json()["sessions"]
    all_sessions = client.get("/api/sessions?scope=all", headers=AUTH_HEADERS).json()["sessions"]

    assert {item["id"] for item in recent} == {first["id"]}
    assert [item["id"] for item in archived] == [second["id"]]
    assert {item["id"] for item in all_sessions} == {first["id"], second["id"]}


def test_get_session_and_404(tmp_path):
    client = TestClient(create_app(settings=make_settings(tmp_path)))
    session = create_session(client)

    detail = client.get(f"/api/sessions/{session['id']}", headers=AUTH_HEADERS)
    missing = client.get("/api/sessions/session_missing", headers=AUTH_HEADERS)

    assert detail.status_code == 200
    assert detail.json()["id"] == session["id"]
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "session_not_found"


def test_update_title_and_archive(tmp_path):
    client = TestClient(create_app(settings=make_settings(tmp_path)))
    session = create_session(client)

    renamed = client.patch(
        f"/api/sessions/{session['id']}",
        json={"title": "Renamed"},
        headers=AUTH_HEADERS,
    )
    archived = client.patch(
        f"/api/sessions/{session['id']}",
        json={"archived": True},
        headers=AUTH_HEADERS,
    )

    assert renamed.json()["title"] == "Renamed"
    assert archived.json()["archived"] is True


def test_add_and_list_messages(tmp_path):
    client = TestClient(create_app(settings=make_settings(tmp_path)))
    session = create_session(client)

    added = client.post(
        f"/api/sessions/{session['id']}/messages",
        json={"role": "user", "text": "hello", "metadata": {"mode": "chat"}},
        headers=AUTH_HEADERS,
    )
    listed = client.get(f"/api/sessions/{session['id']}/messages", headers=AUTH_HEADERS)

    assert added.status_code == 200
    message = added.json()
    assert message["id"].startswith("msg_")
    assert message["session_id"] == session["id"]
    assert message["role"] == "user"
    assert message["metadata"] == {"mode": "chat"}
    assert [item["text"] for item in listed.json()["messages"]] == ["hello"]


def test_add_message_to_missing_session_404(tmp_path):
    client = TestClient(create_app(settings=make_settings(tmp_path)))

    response = client.post(
        "/api/sessions/session_missing/messages",
        json={"role": "user", "text": "hello"},
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 404


def test_delete_session_removes_messages(tmp_path):
    client = TestClient(create_app(settings=make_settings(tmp_path)))
    session = create_session(client)
    client.post(
        f"/api/sessions/{session['id']}/messages",
        json={"role": "user", "text": "hello"},
        headers=AUTH_HEADERS,
    )

    deleted = client.delete(f"/api/sessions/{session['id']}", headers=AUTH_HEADERS)
    missing = client.get(f"/api/sessions/{session['id']}", headers=AUTH_HEADERS)

    assert deleted.json() == {"deleted": True}
    assert missing.status_code == 404


def test_invoke_creates_session_with_auto_title_and_messages(tmp_path):
    client = TestClient(create_app(settings=make_settings(tmp_path)))

    response = client.post(
        "/api/chat/invoke",
        json={"text": "帮我整理今天的项目笔记", "mode": "agent", "attachments": ["a.md"]},
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 200
    session_id = response.json()["session_id"]

    sessions = client.get("/api/sessions?scope=recent", headers=AUTH_HEADERS).json()["sessions"]
    session = next(item for item in sessions if item["id"] == session_id)
    messages = client.get(f"/api/sessions/{session_id}/messages", headers=AUTH_HEADERS).json()[
        "messages"
    ]

    assert session["mode"] == "agent"
    assert session["title"].startswith("帮我整理今天的项目笔记")
    assert [item["role"] for item in messages] == ["user", "assistant"]
    assert messages[0]["text"] == "帮我整理今天的项目笔记"
    assert messages[0]["metadata"]["attachments"] == ["a.md"]
    assert messages[1]["text"] == "收到。"


def test_invoke_reuses_existing_session(tmp_path):
    client = TestClient(create_app(settings=make_settings(tmp_path)))
    session = create_session(client, title="Existing")

    response = client.post(
        "/api/chat/invoke",
        json={"text": "继续", "session_id": session["id"]},
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 200
    assert response.json()["session_id"] == session["id"]
    detail = client.get(f"/api/sessions/{session['id']}", headers=AUTH_HEADERS).json()
    assert detail["title"] == "Existing"
