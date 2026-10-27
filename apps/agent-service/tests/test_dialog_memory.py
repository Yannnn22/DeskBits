"""对话记忆专项测试。

覆盖会话记忆（session scope）的完整生命周期：
写入 → 注入 prompt → 会话隔离 → 更新 → 删除；以及 user/project 跨会话共享、
敏感拒绝、候选确认、记忆事件审计日志。
"""

import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from desktopmate_agent.config import Settings
from desktopmate_agent.main import create_app

AUTH_TOKEN = "test-token"
AUTH_HEADERS = {"X-DesktopMate-Token": AUTH_TOKEN}


def make_settings(tmp_path: Path, **overrides) -> Settings:
    defaults = {
        "trace_dir": tmp_path / "traces",
        "knowledge_db_path": tmp_path / "desktopmate.db",
        "session_db_path": tmp_path / "desktopmate.db",
        "memory_dir": tmp_path / "memory",
        "memory_backend": "sqlite",
        "auth_token": AUTH_TOKEN,
        "openai_api_key": None,
        "deepseek_api_key": None,
    }
    defaults.update(overrides)
    return Settings(**defaults)


def run(client: TestClient, text: str, session_id: str) -> dict:
    response = client.post(
        "/api/chat/invoke",
        json={"text": text, "session_id": session_id},
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 200
    return response.json()


def memory_context(client: TestClient, run_id: str) -> list[dict]:
    events = client.get(f"/api/runs/{run_id}/trace", headers=AUTH_HEADERS).json()["events"]
    payload = next(event["payload"] for event in events if event["type"] == "agent_message")
    return payload.get("memory_context", [])


def test_session_memory_written_and_injected(tmp_path):
    client = TestClient(create_app(settings=make_settings(tmp_path)))
    session = "dialog_session_a"

    written = run(client, "/remember session current_task 实现对话记忆模块", session)
    assert "已记住 current_task" in written["text"]

    reply = run(client, "继续工作", session)
    keys = {item["key"]: item["value"] for item in memory_context(client, reply["run_id"])}

    assert keys.get("current_task") == "实现对话记忆模块"


def test_session_memory_is_isolated_between_sessions(tmp_path):
    client = TestClient(create_app(settings=make_settings(tmp_path)))
    run(client, "/remember session current_task 会话A的任务", "session_a")
    run(client, "/remember session current_task 会话B的任务", "session_b")

    reply_a = run(client, "上下文", "session_a")
    reply_b = run(client, "上下文", "session_b")

    ctx_a = {item["key"]: item["value"] for item in memory_context(client, reply_a["run_id"])}
    ctx_b = {item["key"]: item["value"] for item in memory_context(client, reply_b["run_id"])}
    assert ctx_a.get("current_task") == "会话A的任务"
    assert ctx_b.get("current_task") == "会话B的任务"


def test_user_memory_shared_across_sessions(tmp_path):
    client = TestClient(create_app(settings=make_settings(tmp_path)))
    run(client, "/remember preferred_language 中文", "session_a")

    reply = run(client, "上下文", "session_b")
    keys = {item["key"] for item in memory_context(client, reply["run_id"])}

    assert "preferred_language" in keys


def test_project_memory_injected_when_project_configured(tmp_path):
    client = TestClient(
        create_app(
            settings=make_settings(
                tmp_path,
                memory_project_id="desktopmate",
            )
        )
    )
    run(client, "/remember project stack Vue+FastAPI", "session_a")

    reply = run(client, "上下文", "session_a")
    keys = {item["key"]: item["value"] for item in memory_context(client, reply["run_id"])}

    assert keys.get("stack") == "Vue+FastAPI"


def test_memory_update_replaces_value(tmp_path):
    client = TestClient(create_app(settings=make_settings(tmp_path)))
    run(client, "/remember session current_task 旧任务", "session_a")
    updated = run(client, "/remember session current_task 新任务", "session_a")
    assert "已更新 current_task" in updated["text"]

    reply = run(client, "上下文", "session_a")
    keys = {item["key"]: item["value"] for item in memory_context(client, reply["run_id"])}
    assert keys.get("current_task") == "新任务"


def test_memory_list_and_forget(tmp_path):
    client = TestClient(create_app(settings=make_settings(tmp_path)))
    run(client, "/remember session current_task 测试任务", "session_a")
    run(client, "/remember preferred_language 中文", "session_a")

    listing = run(client, "/memories", "session_a")
    assert "current_task" in listing["text"]
    assert "preferred_language" in listing["text"]

    forgotten = run(client, "/forget session current_task", "session_a")
    assert "已删除 1 条记忆" in forgotten["text"]

    reply = run(client, "上下文", "session_a")
    keys = {item["key"] for item in memory_context(client, reply["run_id"])}
    assert "current_task" not in keys
    assert "preferred_language" in keys


def test_sensitive_memory_rejected_in_dialog(tmp_path):
    client = TestClient(create_app(settings=make_settings(tmp_path)))

    result = run(client, "/remember api_key sk-dialog-secret", "session_a")
    assert "未写入" in result["text"]

    reply = run(client, "上下文", "session_a")
    assert memory_context(client, reply["run_id"]) == []


def test_candidate_memory_approved_then_injected(tmp_path):
    client = TestClient(create_app(settings=make_settings(tmp_path)))
    candidate = run(client, "/remember candidate editor Obsidian", "session_a")["text"]
    assert "候选记忆" in candidate
    candidate_id = candidate.removeprefix("候选记忆 ").split("：")[0]

    approved = run(client, f"/remember approve {candidate_id}", "session_a")
    assert "已记住 editor" in approved["text"] or "已更新 editor" in approved["text"]

    reply = run(client, "上下文", "session_a")
    keys = {item["key"]: item["value"] for item in memory_context(client, reply["run_id"])}
    assert keys.get("editor") == "Obsidian"


def test_memory_events_audit_logged_in_sqlite(tmp_path):
    settings = make_settings(tmp_path)
    client = TestClient(create_app(settings=settings))
    run(client, "/remember session current_task 审计任务", "session_a")
    run(client, "/forget session current_task", "session_a")

    with sqlite3.connect(settings.knowledge_db_path) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT action FROM memory_events ORDER BY created_at"
        ).fetchall()
        items = connection.execute(
            "SELECT status FROM memory_items WHERE key = 'current_task'"
        ).fetchall()

    assert [row["action"] for row in rows] == ["upsert", "delete"]
    assert items[0]["status"] == "deleted"  # 软删，保留审计


def test_session_summary_compression(tmp_path):
    client = TestClient(
        create_app(
            settings=make_settings(
                tmp_path,
                context_compression_enabled=True,
                context_compression_threshold=4,
            )
        )
    )
    session = "compress_session"
    for text in ("我在整理桌面笔记", "我决定用 Python 写小工具", "要支持 Markdown 读取", "先做命令行版本"):
        run(client, text, session)

    with sqlite3.connect(make_settings(tmp_path).knowledge_db_path) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            "SELECT value FROM memory_items WHERE scope = 'session' AND key = 'session_summary'"
        ).fetchone()
    assert row is not None and len(row["value"]) > 0


def test_new_session_starts_without_prior_session_memory(tmp_path):
    client = TestClient(create_app(settings=make_settings(tmp_path)))
    run(client, "/remember session current_task 仅限旧会话", "old_session")

    reply = run(client, "新会话上下文", "brand_new_session")
    ctx = {item["key"] for item in memory_context(client, reply["run_id"])}

    assert "current_task" not in ctx
