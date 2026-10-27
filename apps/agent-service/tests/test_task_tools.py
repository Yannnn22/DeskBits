"""Agent tools for tasks and reminders."""

import asyncio
from pathlib import Path

from fastapi.testclient import TestClient

from desktopmate_agent.config import Settings
from desktopmate_agent.main import create_app
from desktopmate_agent.reminders.service import ReminderService
from desktopmate_agent.reminders.store import ReminderStore
from desktopmate_agent.memory.store import JsonlMemoryStore
from desktopmate_agent.tasks.store import TaskStore
from desktopmate_agent.tools.task_tools import (
    ReminderCreateArgs,
    ReminderCreateTool,
    TaskConfirmArgs,
    TaskConfirmTool,
    TaskDraftArgs,
    TaskDraftTool,
    TaskCreateArgs,
    TaskCreateTool,
    TaskListArgs,
    TaskListTool,
    TaskUpdateArgs,
    TaskUpdateTool,
)

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


def test_task_tools_create_update_and_list(tmp_path):
    store = TaskStore(tmp_path / "tasks.db")
    create_tool = TaskCreateTool(store=store)
    update_tool = TaskUpdateTool(store=store)
    list_tool = TaskListTool(store=store)

    created = asyncio.run(
        create_tool.run(
            TaskCreateArgs(
                title="整理会议纪要",
                description="提取结论、风险和下一步",
                due_at="2026-08-07T18:00:00",
            )
        )
    )
    task = created.data["task"]
    assert created.status == "success"
    assert task["title"] == "整理会议纪要"
    assert task["description"] == "提取结论、风险和下一步"
    assert task["due_at"] == "2026-08-07T18:00:00"
    assert task["status"] == "todo"

    updated = asyncio.run(
        update_tool.run(TaskUpdateArgs(task_id=task["id"], status="in_progress"))
    )
    assert updated.status == "success"
    assert updated.data["task"]["status"] == "in_progress"

    listed = asyncio.run(list_tool.run(TaskListArgs(status="in_progress")))
    assert listed.status == "success"
    assert [item["id"] for item in listed.data["tasks"]] == [task["id"]]


def test_reminder_create_tool(tmp_path):
    service = ReminderService(ReminderStore(tmp_path / "reminders.db"))
    tool = ReminderCreateTool(service=service)

    created = asyncio.run(
        tool.run(
            ReminderCreateArgs(
                title="站会提醒",
                body="同步昨天进展和今天风险",
                schedule_type="daily",
                time_of_day="09:30",
                timezone="Asia/Shanghai",
            )
        )
    )

    reminder = created.data["reminder"]
    assert created.status == "success"
    assert reminder["title"] == "站会提醒"
    assert reminder["schedule_type"] == "daily"
    assert reminder["time_of_day"] == "09:30"


def test_task_draft_tool_returns_a_candidate_without_writing(tmp_path):
    tool = TaskDraftTool()

    candidate = asyncio.run(
        tool.run(
            TaskDraftArgs(
                title="吃药",
                description="提醒我明天下午一点吃药",
                due_at="2026-08-09T13:00:00+08:00",
                create_reminder=True,
            )
        )
    )

    assert candidate.status == "success"
    assert candidate.data["task"]["status"] == "todo"
    assert candidate.data["reminder"]["due_at"] == "2026-08-09T13:00:00+08:00"


def test_task_confirm_tool_writes_task_reminder_and_session_memory(tmp_path):
    task_store = TaskStore(tmp_path / "tasks.db")
    reminder_service = ReminderService(ReminderStore(tmp_path / "reminders.db"))
    memory_store = JsonlMemoryStore(tmp_path / "memory")
    tool = TaskConfirmTool(
        store=task_store,
        reminder_service=reminder_service,
        memory_store=memory_store,
    )

    confirmed = asyncio.run(
        tool.run(
            TaskConfirmArgs(
                session_id="session_health",
                title="按时服用药物",
                description="午餐后服用当天药物。",
                due_at="2026-08-09T13:00:00+08:00",
                create_reminder=True,
                reminder_message="午餐后该服药了。",
                memory_summary="已安排明天下午一点服药。",
            )
        )
    )

    assert confirmed.status == "success"
    assert confirmed.data["task"]["reminder_id"] == confirmed.data["reminder"]["id"]
    assert confirmed.data["memory"] == {
        "scope": "session",
        "key": f"task:{confirmed.data['task']['id']}",
        "value": "已安排明天下午一点服药。",
    }
    entries = memory_store.read_session("session_health")
    assert entries[0].source == "task_confirmed"


def test_task_tools_are_registered_in_app(tmp_path):
    client = TestClient(create_app(settings=make_settings(tmp_path)))

    skills = client.get("/api/skills", headers=AUTH_HEADERS).json()["skills"]
    names = {item["name"] for item in skills}

    assert {"task_create", "task_draft", "task_update", "task_list", "reminder_create"} <= names
