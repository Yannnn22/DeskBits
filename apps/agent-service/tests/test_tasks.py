"""task/todo 统一模型：/api/tasks CRUD。"""

from pathlib import Path

from fastapi.testclient import TestClient

from desktopmate_agent.config import Settings
from desktopmate_agent.main import create_app

AUTH_HEADERS = {"X-DesktopMate-Token": "test-token"}


def make_settings(tmp_path: Path) -> Settings:
    return Settings(
        trace_dir=tmp_path / "traces",
        settings_dir=tmp_path / "settings",
        app_data_dir=tmp_path,
        knowledge_dir=tmp_path / "knowledge",
        knowledge_db_path=tmp_path / "desktopmate.db",
        session_db_path=tmp_path / "desktopmate.db",
        auth_token="test-token",
        openai_api_key=None,
        deepseek_api_key=None,
        embedding_provider="fake",
        embedding_dimensions=16,
        allowed_roots=str(tmp_path),
    )


def test_tasks_crud(tmp_path):
    client = TestClient(create_app(settings=make_settings(tmp_path)))

    # 空列表
    empty = client.get("/api/tasks", headers=AUTH_HEADERS).json()
    assert empty == {"tasks": []}

    # 创建
    created = client.post(
        "/api/tasks",
        json={"title": "写周报", "due_at": "2026-08-07T09:00:00"},
        headers=AUTH_HEADERS,
    )
    assert created.status_code == 200
    task = created.json()
    assert task["title"] == "写周报"
    assert task["status"] == "todo"
    assert task["due_at"] == "2026-08-07T09:00:00"
    assert task["reminder_id"] is None
    assert task["created_at"]
    assert task["completed_at"] is None

    linked = client.patch(
        f"/api/tasks/{task['id']}",
        json={"reminder_id": "reminder_example"},
        headers=AUTH_HEADERS,
    )
    assert linked.status_code == 200
    assert linked.json()["reminder_id"] == "reminder_example"

    cleared_due = client.patch(
        f"/api/tasks/{task['id']}",
        json={"due_at": None},
        headers=AUTH_HEADERS,
    )
    assert cleared_due.status_code == 200
    assert cleared_due.json()["due_at"] is None

    # 校验：空标题
    bad = client.post("/api/tasks", json={"title": ""}, headers=AUTH_HEADERS)
    assert bad.status_code == 422

    # 完成（打勾）
    done = client.patch(f"/api/tasks/{task['id']}", json={"status": "done"}, headers=AUTH_HEADERS)
    assert done.status_code == 200
    assert done.json()["status"] == "done"
    assert done.json()["completed_at"] is not None

    # 列表含完成项 + 状态过滤
    listed = client.get("/api/tasks", headers=AUTH_HEADERS).json()["tasks"]
    assert len(listed) == 1
    assert listed[0]["status"] == "done"
    todo_only = client.get("/api/tasks?status=todo", headers=AUTH_HEADERS).json()["tasks"]
    assert todo_only == []

    # 非法状态
    invalid = client.patch(f"/api/tasks/{task['id']}", json={"status": "archived"}, headers=AUTH_HEADERS)
    assert invalid.status_code == 422

    # 删除
    deleted = client.delete(f"/api/tasks/{task['id']}", headers=AUTH_HEADERS)
    assert deleted.status_code == 200
    assert deleted.json() == {"deleted": True}
    assert client.get("/api/tasks", headers=AUTH_HEADERS).json() == {"tasks": []}

    # 不存在
    assert client.delete(f"/api/tasks/{task['id']}", headers=AUTH_HEADERS).status_code == 404
    assert client.patch(f"/api/tasks/{task['id']}", json={"status": "done"}, headers=AUTH_HEADERS).status_code == 404


def test_tasks_sorting_and_ordering(tmp_path):
    client = TestClient(create_app(settings=make_settings(tmp_path)))
    for title, due in [("早任务", "2026-08-07T08:00:00"), ("晚任务", "2026-08-07T18:00:00")]:
        client.post("/api/tasks", json={"title": title, "due_at": due}, headers=AUTH_HEADERS)

    listed = client.get("/api/tasks", headers=AUTH_HEADERS).json()["tasks"]
    assert [t["title"] for t in listed] == ["早任务", "晚任务"]

    # 完成项排最后
    client.patch(f"/api/tasks/{listed[0]['id']}", json={"status": "done"}, headers=AUTH_HEADERS)
    re_listed = client.get("/api/tasks", headers=AUTH_HEADERS).json()["tasks"]
    assert [t["title"] for t in re_listed] == ["晚任务", "早任务"]


def test_confirm_draft_uses_task_tool_and_writes_session_memory(tmp_path):
    app = create_app(settings=make_settings(tmp_path))
    client = TestClient(app)

    confirmed = client.post(
        "/api/tasks/confirm-draft",
        json={
            "session_id": "session_health",
            "title": "按时服用药物",
            "description": "午餐后服用当天药物。",
            "due_at": "2026-08-09T13:00:00+08:00",
            "timezone": "Asia/Shanghai",
            "create_reminder": True,
            "category": "健康",
            "priority": "high",
            "next_action": "把药放到午餐位置。",
            "reminder_message": "午餐后该服药了。",
            "memory_summary": "已安排明天下午一点服药。",
        },
        headers=AUTH_HEADERS,
    )

    assert confirmed.status_code == 200
    body = confirmed.json()
    assert body["task"]["reminder_id"] == body["reminder"]["id"]
    assert body["task"]["category"] == "健康"
    assert body["task"]["priority"] == "high"
    assert body["task"]["next_action"] == "把药放到午餐位置。"
    assert body["task"]["source_session_id"] == "session_health"
    assert body["memory"]["value"] == "已安排明天下午一点服药。"
    entries = app.state.agent_service.memory_store.read_session("session_health")
    assert entries[0].value == "已安排明天下午一点服药。"
