"""任务存储：SQLite tasks 表（task/todo 统一模型）。

与提醒（reminders）并存：任务面向「今日待办/任务编排」，提醒保留定时通知语义。
"""

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

VALID_STATUSES = ("todo", "in_progress", "done")
_UNSET = object()


class Task:
    def __init__(
        self,
        *,
        id: str,
        title: str,
        description: str | None,
        due_at: str | None,
        reminder_id: str | None,
        category: str,
        priority: str,
        next_action: str | None,
        source_session_id: str | None,
        status: str,
        created_at: str,
        completed_at: str | None,
    ) -> None:
        self.id = id
        self.title = title
        self.description = description
        self.due_at = due_at
        self.reminder_id = reminder_id
        self.category = category
        self.priority = priority
        self.next_action = next_action
        self.source_session_id = source_session_id
        self.status = status
        self.created_at = created_at
        self.completed_at = completed_at

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "due_at": self.due_at,
            "reminder_id": self.reminder_id,
            "category": self.category,
            "priority": self.priority,
            "next_action": self.next_action,
            "source_session_id": self.source_session_id,
            "status": self.status,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
        }


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _row_to_task(row: sqlite3.Row) -> Task:
    return Task(
        id=str(row["id"]),
        title=str(row["title"]),
        description=row["description"],
        due_at=row["due_at"],
        reminder_id=row["reminder_id"],
        category=str(row["category"] or "个人"),
        priority=str(row["priority"] or "normal"),
        next_action=row["next_action"],
        source_session_id=row["source_session_id"],
        status=str(row["status"]),
        created_at=str(row["created_at"]),
        completed_at=row["completed_at"],
    )


class TaskStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    def _init_db(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    description TEXT,
                    due_at TEXT,
                    reminder_id TEXT,
                    category TEXT NOT NULL DEFAULT '个人',
                    priority TEXT NOT NULL DEFAULT 'normal',
                    next_action TEXT,
                    source_session_id TEXT,
                    status TEXT NOT NULL DEFAULT 'todo',
                    created_at TEXT NOT NULL,
                    completed_at TEXT
                )
                """
            )
            columns = {str(row["name"]) for row in connection.execute("PRAGMA table_info(tasks)")}
            if "reminder_id" not in columns:
                connection.execute("ALTER TABLE tasks ADD COLUMN reminder_id TEXT")
            if "category" not in columns:
                connection.execute("ALTER TABLE tasks ADD COLUMN category TEXT NOT NULL DEFAULT '个人'")
            if "priority" not in columns:
                connection.execute("ALTER TABLE tasks ADD COLUMN priority TEXT NOT NULL DEFAULT 'normal'")
            if "next_action" not in columns:
                connection.execute("ALTER TABLE tasks ADD COLUMN next_action TEXT")
            if "source_session_id" not in columns:
                connection.execute("ALTER TABLE tasks ADD COLUMN source_session_id TEXT")

    def create(
        self,
        *,
        title: str,
        description: str | None = None,
        due_at: str | None = None,
        reminder_id: str | None = None,
        category: str = "个人",
        priority: str = "normal",
        next_action: str | None = None,
        source_session_id: str | None = None,
    ) -> Task:
        task_id = f"task_{uuid4().hex}"
        now = _now_iso()
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO tasks (id, title, description, due_at, reminder_id, category, priority, next_action, source_session_id, status, created_at, completed_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'todo', ?, NULL)",
                (task_id, title, description, due_at, reminder_id, category, priority, next_action, source_session_id, now),
            )
        return Task(
            id=task_id,
            title=title,
            description=description,
            due_at=due_at,
            reminder_id=reminder_id,
            category=category,
            priority=priority,
            next_action=next_action,
            source_session_id=source_session_id,
            status="todo",
            created_at=now,
            completed_at=None,
        )

    def list(self, status: str | None = None) -> list[Task]:
        query = "SELECT * FROM tasks"
        params: tuple = ()
        if status:
            query += " WHERE status = ?"
            params = (status,)
        query += " ORDER BY CASE status WHEN 'todo' THEN 0 WHEN 'in_progress' THEN 1 ELSE 2 END,"
        query += " COALESCE(due_at, created_at) ASC"
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [_row_to_task(row) for row in rows]

    def get(self, task_id: str) -> Task | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return _row_to_task(row) if row else None

    def update(
        self,
        task_id: str,
        *,
        title: str | None = None,
        description: str | None | object = _UNSET,
        due_at: str | None | object = _UNSET,
        reminder_id: str | None | object = _UNSET,
        category: str | None = None,
        priority: str | None = None,
        next_action: str | None | object = _UNSET,
        source_session_id: str | None = None,
        status: str | None = None,
    ) -> Task | None:
        task = self.get(task_id)
        if task is None:
            return None
        new_title = title if title is not None else task.title
        new_description = task.description if description is _UNSET else description
        new_due_at = task.due_at if due_at is _UNSET else due_at
        new_reminder_id = task.reminder_id if reminder_id is _UNSET else reminder_id
        new_category = category if category is not None else task.category
        new_priority = priority if priority is not None else task.priority
        new_next_action = task.next_action if next_action is _UNSET else next_action
        new_source_session_id = source_session_id if source_session_id is not None else task.source_session_id
        new_status = status if status is not None else task.status
        completed_at = task.completed_at
        if new_status == "done" and task.status != "done":
            completed_at = _now_iso()
        elif new_status != "done":
            completed_at = None
        with self._connect() as connection:
            connection.execute(
                "UPDATE tasks SET title = ?, description = ?, due_at = ?, reminder_id = ?, category = ?, priority = ?, next_action = ?, source_session_id = ?, status = ?, completed_at = ? WHERE id = ?",
                (new_title, new_description, new_due_at, new_reminder_id, new_category, new_priority, new_next_action, new_source_session_id, new_status, completed_at, task_id),
            )
        return self.get(task_id)

    def delete(self, task_id: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        return cursor.rowcount > 0
