"""待办与提醒服务：到期判定、触发、runtime 事件发布与后台调度。

触发语义：
- ``once``：``due_at <= now`` 即触发；触发后状态转 ``done``。
- ``daily``：当天到达 ``time_of_day`` 后触发一次（同一天不重复）。
- ``weekly``：到达 ``time_of_day`` 且距上次触发 >= 7 天时触发。
- dismiss：once 转 ``done``；daily/weekly 只跳过本次（写 last_fired_at）。

到期扫描由 ``ReminderScheduler`` 每 30 秒驱动，触发时写入
``reminder_events`` 并发布 ``reminder_due`` runtime 事件（供桌宠订阅）。
"""

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from desktopmate_agent.reminders.store import (
    Reminder,
    ReminderScheduleType,
    ReminderStore,
)
from desktopmate_agent.runtime.events import RuntimeEventBus

logger = logging.getLogger(__name__)

DEFAULT_SCHEDULER_INTERVAL_SECONDS = 30


def _parse_iso(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo("UTC"))
    return parsed


def _parse_time_of_day(value: str) -> tuple[int, int]:
    hour_text, _, minute_text = value.partition(":")
    try:
        hour = int(hour_text)
        minute = int(minute_text or "0")
    except ValueError as exc:
        raise ValueError(f"无效的 time_of_day: {value!r}") from exc
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"无效的 time_of_day: {value!r}")
    return hour, minute


class ReminderService:
    def __init__(
        self,
        store: ReminderStore,
        runtime_events: RuntimeEventBus | None = None,
    ) -> None:
        self.store = store
        self.runtime_events = runtime_events

    # --- CRUD（带事件日志） ---

    def create(
        self,
        *,
        title: str,
        schedule_type: ReminderScheduleType,
        timezone: str = "UTC",
        body: str | None = None,
        due_at: str | None = None,
        time_of_day: str | None = None,
        day_of_week: int | None = None,
    ) -> Reminder:
        _validate_schedule(
            schedule_type,
            due_at=due_at,
            time_of_day=time_of_day,
            day_of_week=day_of_week,
        )
        reminder = self.store.create(
            title=title,
            body=body,
            schedule_type=schedule_type,
            due_at=due_at,
            time_of_day=time_of_day,
            timezone=timezone,
            day_of_week=day_of_week,
        )
        self.store.log_event(reminder.id, "created", {"schedule_type": schedule_type})
        return reminder

    def update(self, reminder_id: str, patch: dict[str, Any]) -> Reminder | None:
        reminder = self.store.get(reminder_id)
        if reminder is None:
            return None
        schedule_type = patch.get("schedule_type", reminder.schedule_type)
        day_of_week = patch.get("day_of_week", reminder.day_of_week)
        _validate_schedule(
            schedule_type,
            due_at=patch.get("due_at", reminder.due_at),
            time_of_day=patch.get("time_of_day", reminder.time_of_day),
            day_of_week=day_of_week,
        )
        updated = self.store.update(reminder_id, **patch)
        if updated is not None:
            self.store.log_event(reminder_id, "updated", {"fields": list(patch)})
        return updated

    def dismiss(self, reminder_id: str) -> Reminder | None:
        """本次提醒已读但不完成：once 转 done；daily/weekly 跳过本次。"""
        reminder = self.store.get(reminder_id)
        if reminder is None:
            return None
        if reminder.schedule_type == "once":
            updated = self.store.set_status(reminder_id, "done")
        else:
            updated = self.store.set_last_fired(reminder_id, _now_iso())
        self.store.log_event(reminder_id, "dismissed", {"schedule_type": reminder.schedule_type})
        return updated

    def complete(self, reminder_id: str) -> Reminder | None:
        reminder = self.store.get(reminder_id)
        if reminder is None:
            return None
        updated = self.store.set_status(reminder_id, "done")
        self.store.log_event(reminder_id, "completed", {})
        return updated

    def delete(self, reminder_id: str) -> bool:
        deleted = self.store.delete(reminder_id)
        if deleted:
            self.store.log_event(reminder_id, "updated", {"status": "deleted"})
        return deleted

    # --- 到期判定与触发 ---

    def is_due(self, reminder: Reminder, now: datetime | None = None) -> bool:
        """按调度类型判定是否应触发。``now`` 未传时用当前 UTC 时间。"""
        if reminder.status != "active":
            return False
        now = now or datetime.now(UTC)
        if reminder.schedule_type == "once":
            if not reminder.due_at:
                return False
            return _parse_iso(reminder.due_at) <= now
        # daily / weekly
        if not reminder.time_of_day:
            return False
        try:
            timezone = ZoneInfo(reminder.timezone)
        except Exception:
            timezone = ZoneInfo("UTC")
        local_now = now.astimezone(timezone)
        hour, minute = _parse_time_of_day(reminder.time_of_day)
        fire_time = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if local_now < fire_time:
            return False  # 还没到点
        if reminder.schedule_type == "weekly" and reminder.day_of_week is not None:
            # 指定了星期几：只在匹配的星期触发（0=周一 .. 6=周日）
            if local_now.weekday() != reminder.day_of_week:
                return False
        if reminder.last_fired_at:
            last_fired = _parse_iso(reminder.last_fired_at).astimezone(timezone)
            if reminder.schedule_type == "daily" and last_fired.date() == local_now.date():
                return False  # 同一天已触发
            if reminder.schedule_type == "weekly" and (local_now - last_fired).days < 7:
                return False  # 7 天内已触发
        return True

    def due_reminders(self, now: datetime | None = None) -> list[Reminder]:
        now = now or datetime.now(UTC)
        return [
            reminder
            for reminder in self.store.list(status="active")
            if self.is_due(reminder, now)
        ]

    def due_reminders_count(self, now: datetime | None = None) -> int:
        return len(self.due_reminders(now))

    async def scan_and_fire(self, now: datetime | None = None) -> list[Reminder]:
        """扫描并触发所有到期提醒；返回本次触发的提醒列表。

        ``now`` 未传时用当前 UTC 时间；触发时间戳与扫描时间保持一致，
        保证同天去重逻辑在测试注入时间时同样确定。
        """
        now = now or datetime.now(UTC)
        fired: list[Reminder] = []
        for reminder in self.due_reminders(now):
            await self._fire(reminder, now)
            fired.append(reminder)
        return fired

    async def _fire(self, reminder: Reminder, now: datetime) -> None:
        fired_at = now.isoformat()
        self.store.set_last_fired(reminder.id, fired_at)
        self.store.log_event(
            reminder.id,
            "fired",
            {"schedule_type": reminder.schedule_type, "time": fired_at},
        )
        if reminder.schedule_type == "once":
            self.store.set_status(reminder.id, "done")
        if self.runtime_events is not None:
            await self.runtime_events.append(
                "reminder_due",
                {
                    "reminder_id": reminder.id,
                    "title": reminder.title,
                    "schedule_type": reminder.schedule_type,
                },
            )


class ReminderScheduler:
    """后台调度器：每 ``interval_seconds`` 扫描一次到期提醒。"""

    def __init__(
        self,
        service: ReminderService,
        interval_seconds: int = DEFAULT_SCHEDULER_INTERVAL_SECONDS,
    ) -> None:
        self.service = service
        self.interval_seconds = interval_seconds
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self._loop())

    def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        self._task = None

    async def _loop(self) -> None:
        while True:
            try:
                await self.service.scan_and_fire()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("reminder scan failed")
            await asyncio.sleep(self.interval_seconds)


def _validate_schedule(
    schedule_type: str,
    *,
    due_at: str | None,
    time_of_day: str | None,
    day_of_week: int | None = None,
) -> None:
    if schedule_type not in {"once", "daily", "weekly"}:
        raise ValueError(f"无效的 schedule_type: {schedule_type!r}")
    if schedule_type == "once" and not due_at:
        raise ValueError("once 类型必须提供 due_at")
    if schedule_type in {"daily", "weekly"} and not time_of_day:
        raise ValueError(f"{schedule_type} 类型必须提供 time_of_day")
    if time_of_day:
        _parse_time_of_day(time_of_day)
    if day_of_week is not None and not (0 <= day_of_week <= 6):
        raise ValueError(f"无效的 day_of_week: {day_of_week!r}（0=周一 .. 6=周日）")
    if day_of_week is not None and schedule_type != "weekly":
        raise ValueError("day_of_week 仅适用于 weekly 提醒")


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()
