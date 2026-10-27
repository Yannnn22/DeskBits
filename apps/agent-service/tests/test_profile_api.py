from pathlib import Path

from fastapi.testclient import TestClient

from desktopmate_agent.config import Settings
from desktopmate_agent.main import create_app

AUTH_TOKEN = "test-token"
AUTH_HEADERS = {"X-DesktopMate-Token": AUTH_TOKEN}


def make_settings(tmp_path: Path) -> Settings:
    return Settings(
        trace_dir=tmp_path / "traces",
        knowledge_db_path=tmp_path / "desktopmate.db",
        session_db_path=tmp_path / "desktopmate.db",
        auth_token=AUTH_TOKEN,
        openai_api_key=None,
        deepseek_api_key=None,
    )


def invoke(client: TestClient, text: str, session_id: str = "profile_session") -> str:
    response = client.post(
        "/api/chat/invoke",
        json={"text": text, "session_id": session_id},
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 200
    return response.json()["text"]


def test_profile_aggregates_memory_into_facts(tmp_path):
    settings = make_settings(tmp_path)
    client = TestClient(create_app(settings=settings))
    client.put(
        "/api/settings/profile",
        json={"display_name": "小测", "data_dir": "/tmp/x"},
        headers=AUTH_HEADERS,
    )

    invoke(client, "/remember preferred_language 中文")
    invoke(client, "/remember timezone Asia/Shanghai")
    invoke(client, "/remember api_key sk-secret")  # 敏感，被拒绝

    profile = client.get("/api/profile", headers=AUTH_HEADERS).json()

    assert profile["profile"]["display_name"] == "小测"
    assert profile["profile"]["preferred_language"] == "中文"
    assert profile["profile"]["timezone"] == "Asia/Shanghai"
    assert "api_key" not in profile["profile"]

    facts = profile["facts"]
    fields = {fact["field"]: fact for fact in facts}
    assert "preferred_language" in fields
    assert "timezone" in fields
    assert fields["preferred_language"]["source_memory_id"] is not None
    assert fields["preferred_language"]["confidence"] == 1.0


def test_profile_facts_are_stored_in_sqlite(tmp_path):
    settings = make_settings(tmp_path)
    client = TestClient(create_app(settings=settings))
    invoke(client, "/remember editor Obsidian")

    client.get("/api/profile", headers=AUTH_HEADERS)

    import sqlite3

    with sqlite3.connect(settings.knowledge_db_path) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            "SELECT * FROM profile_facts WHERE field = 'editor'"
        ).fetchone()
    assert row is not None
    assert row["value"] == "Obsidian"
    assert row["source_memory_id"].startswith("mem_")


def test_profile_refresh_rebuilds_facts(tmp_path):
    client = TestClient(create_app(settings=make_settings(tmp_path)))
    invoke(client, "/remember preferred_language 中文")
    client.get("/api/profile", headers=AUTH_HEADERS)

    invoke(client, "/forget user preferred_language")
    refreshed = client.post("/api/profile/refresh", headers=AUTH_HEADERS).json()

    assert "preferred_language" not in refreshed["profile"]
    assert all(fact["field"] != "preferred_language" for fact in refreshed["facts"])


def test_profile_requires_auth(tmp_path):
    client = TestClient(create_app(settings=make_settings(tmp_path)))

    assert client.get("/api/profile").status_code == 401
