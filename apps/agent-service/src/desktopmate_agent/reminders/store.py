"""待办与提醒的 SQLite 存储。

与 sessions / knowledge 同模式：标准库 sqlite3，每次操作独立连接，
避免跨线程共享连接。表结构见计划 S3：

- ``reminders``：提醒本身（once | daily | weekly）。
- ``reminder_events``：提醒生命周期事件日志（created | updated | fired |
  dismissed | completed）。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel

ReminderStatus = Literal["active", "paused", "done", "deleted"]
ReminderScheduleType = Literal["once", "daily", "weekly"]

VALID_STATUSES: set[str] = {"active", "paused", "done", "deleted"}
VALID_SCHEDULE_TYPES: set[str] = {"once", "daily", "weekly"}
VALID_EVENT_TYPES: set[str] = {"created", "updated", "fired", "dismissed", "completed"}


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


class Reminder(BaseModel):
    id: str
    title: str
    body: str | None = None
    schedule_type: ReminderScheduleType
    due_at: str | None = None
    time_of_day: str | None = None
    # weekly 专用：0=周一 .. 6=周日。None = 固定 7 天间隔（旧行为，向后兼容）
    day_of_week: int | None = None
    timezone: str = "UTC"
    status: ReminderStatus = "active"
    created_at: str
    updated_at: str
    last_fired_at: str | None = None


class ReminderStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _init_schema(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS reminders (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    body TEXT,
                    schedule_type TEXT NOT NULL,
                    due_at TEXT,
                    time_of_day TEXT,
                    day_of_week INTEGER,
                    timezone TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_fired_at TEXT
                )
                """
            )
            # 旧库升级：已有 reminders 表补充 day_of_week 列（幂等）
            columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(reminders)").fetchall()
            }
            if "day_of_week" not in columns:
                connection.execute("ALTER TABLE reminders ADD COLUMN day_of_week INTEGER")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS reminder_events (
                    id TEXT PRIMARY KEY,
                    reminder_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_reminders_status ON reminders(status)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_reminder_events_reminder ON reminder_events(reminder_id)"
            )

    # --- 基础读写 ---

    def _row_to_reminder(self, row: sqlite3.Row) -> Reminder:
        return Reminder(**dict(row))

    def get(self, reminder_id: str) -> Reminder | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM reminders WHERE id = ?", (reminder_id,)
            ).fetchone()
        return self._row_to_reminder(row) if row is not None else None

    def list(self, status: str = "active") -> list[Reminder]:
        with self._connect() as connection:
            if status == "all":
                rows = connection.execute(
                    "SELECT * FROM reminders ORDER BY created_at DESC"
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM reminders WHERE status = ? ORDER BY created_at DESC",
                    (status,),
                ).fetchall()
        return [self._row_to_reminder(row) for row in rows]

    def create(
        self,
        *,
        title: str,
        body: str | None,
        schedule_type: ReminderScheduleType,
        due_at: str | None,
        time_of_day: str | None,
        timezone: str,
        day_of_week: int | None = None,
    ) -> Reminder:
        now = _now_iso()
        reminder = Reminder(
            id=_new_id("reminder"),
            title=title,
            body=body,
            schedule_type=schedule_type,
            due_at=due_at,
            time_of_day=time_of_day,
            day_of_week=day_of_week,
            timezone=timezone,
            status="active",
            created_at=now,
            updated_at=now,
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO reminders (
                    id, title, body, schedule_type, due_at, time_of_day, day_of_week,
                    timezone, status, created_at, updated_at, last_fired_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    reminder.id,
                    reminder.title,
                    reminder.body,
                    reminder.schedule_type,
                    reminder.due_at,
                    reminder.time_of_day,
                    reminder.day_of_week,
                    reminder.timezone,
                    reminder.status,
                    reminder.created_at,
                    reminder.updated_at,
                    reminder.last_fired_at,
                ),
            )
        return reminder

    def update(self, reminder_id: str, **fields: Any) -> Reminder | None:
        """按白名单字段更新，返回更新后的 Reminder（不存在返回 None）。"""
        allowed = {
            "title",
            "body",
            "schedule_type",
            "due_at",
            "time_of_day",
            "timezone",
            "status",
            "last_fired_at",
            "day_of_week",
        }
        updates = {key: value for key, value in fields.items() if key in allowed}
        if not updates:
            return self.get(reminder_id)
        updates["updated_at"] = _now_iso()
        assignments = ", ".join(f"{key} = ?" for key in updates)
        values = list(updates.values()) + [reminder_id]
        with self._connect() as connection:
            cursor = connection.execute(
                f"UPDATE reminders SET {assignments} WHERE id = ?", values
            )
            if cursor.rowcount == 0:
                return None
        return self.get(reminder_id)

    def set_status(self, reminder_id: str, status: ReminderStatus) -> Reminder | None:
        return self.update(reminder_id, status=status)

    def set_last_fired(self, reminder_id: str, fired_at: str) -> Reminder | None:
        return self.update(reminder_id, last_fired_at=fired_at)

    def delete(self, reminder_id: str) -> bool:
        """软删：status=deleted，记录仍在库中。"""
        reminder = self.set_status(reminder_id, "deleted")
        return reminder is not None

    def log_event(
        self,
        reminder_id: str,
        event_type: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO reminder_events (id, reminder_id, event_type, created_at, payload_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    _new_id("evt"),
                    reminder_id,
                    event_type,
                    _now_iso(),
                    json.dumps(payload or {}, ensure_ascii=False),
                ),
            )

    def events_for(self, reminder_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM reminder_events WHERE reminder_id = ? ORDER BY created_at ASC",
                (reminder_id,),
            ).fetchall()
        return [dict(row) for row in rows]
