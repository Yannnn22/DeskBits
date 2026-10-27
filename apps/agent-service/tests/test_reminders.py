"""S3 待办与提醒：存储、到期判定、触发、runtime 事件与 API。"""

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from desktopmate_agent.config import Settings
from desktopmate_agent.main import create_app
from desktopmate_agent.reminders.service import ReminderScheduler, ReminderService
from desktopmate_agent.reminders.store import ReminderStore
from desktopmate_agent.runtime.events import RuntimeEventBus

AUTH_TOKEN = "test-token"
AUTH_HEADERS = {"X-DesktopMate-Token": AUTH_TOKEN}

PAST_DUE = "2020-01-01T00:00:00+00:00"


def make_settings(tmp_path: Path, **overrides) -> Settings:
    defaults = {
        "trace_dir": tmp_path / "traces",
        "settings_dir": tmp_path / "settings",
        "auth_token": AUTH_TOKEN,
        "openai_api_key": None,
        "deepseek_api_key": None,
    }
    defaults.update(overrides)
    return Settings(**defaults)


def make_service(tmp_path: Path, bus: RuntimeEventBus | None = None) -> ReminderService:
    return ReminderService(ReminderStore(tmp_path / "reminders.db"), runtime_events=bus)


# --- 存储 ---


def test_store_crud_and_persistence(tmp_path):
    store = ReminderStore(tmp_path / "reminders.db")
    reminder = store.create(
        title="喝水",
        body="多喝水",
        schedule_type="daily",
        due_at=None,
        time_of_day="09:30",
        timezone="Asia/Shanghai",
    )
    assert reminder.status == "active"
    assert store.get(reminder.id).title == "喝水"

    # 重启后仍在（同一 db 文件）
    reopened = ReminderStore(tmp_path / "reminders.db")
    assert reopened.get(reminder.id).title == "喝水"
    assert [item.id for item in reopened.list()] == [reminder.id]

    # 软删
    assert reopened.delete(reminder.id) is True
    assert reopened.get(reminder.id).status == "deleted"
    assert reopened.list() == []
    assert reopened.list(status="all")[0].status == "deleted"
    assert reopened.delete("missing") is False


def test_store_logs_events(tmp_path):
    store = ReminderStore(tmp_path / "reminders.db")
    service = ReminderService(store)
    reminder = service.create(
        title="x", body=None, schedule_type="once", due_at=PAST_DUE,
        time_of_day=None, timezone="UTC",
    )
    store.log_event(reminder.id, "fired", {"time": "t"})
    store.log_event(reminder.id, "completed", {})
    events = store.events_for(reminder.id)
    assert [e["event_type"] for e in events] == ["created", "fired", "completed"]
    assert "t" in events[1]["payload_json"]


# --- 到期判定 ---


def test_once_due_only_when_past_due(tmp_path):
    service = make_service(tmp_path)
    past = service.create(title="a", schedule_type="once", due_at=PAST_DUE)
    future = service.create(
        title="b", schedule_type="once",
        due_at="2999-01-01T00:00:00+00:00",
    )
    now = datetime(2026, 8, 5, 10, 0, tzinfo=UTC)
    assert service.is_due(past, now) is True
    assert service.is_due(future, now) is False


def test_daily_due_after_time_of_day_once_per_day(tmp_path):
    service = make_service(tmp_path)
    reminder = service.create(
        title="晨会", schedule_type="daily",
        time_of_day="09:00", timezone="Asia/Shanghai",
    )
    # 2026-08-05T00:30Z = 北京 08:30，未到 09:00
    early = datetime(2026, 8, 5, 0, 30, tzinfo=UTC)
    assert service.is_due(reminder, early) is False
    # 09:30Z = 北京 17:30，已过 09:00
    late = datetime(2026, 8, 5, 9, 30, tzinfo=UTC)
    assert service.is_due(reminder, late) is True
    # 次日仍到期
    assert service.is_due(reminder, late + timedelta(days=1)) is True


# --- 触发与 runtime 事件 ---


async def test_scan_fires_due_once_and_publishes_runtime_event(tmp_path):
    bus = RuntimeEventBus(path=None)
    store = ReminderStore(tmp_path / "reminders.db")
    service = ReminderService(store, runtime_events=bus)
    now = datetime(2026, 8, 5, 10, 0, tzinfo=UTC)
    reminder = service.create(title="吃药", schedule_type="once", due_at=now.isoformat())

    fired = await service.scan_and_fire(now=now)
    assert [item.id for item in fired] == [reminder.id]
    assert store.get(reminder.id).status == "done"

    events, _ = bus.events_since(0)
    assert events[-1]["type"] == "reminder_due"
    assert events[-1]["payload"]["title"] == "吃药"
    assert store.events_for(reminder.id)[-1]["event_type"] == "fired"

    # 已 done，不再触发
    assert await service.scan_and_fire(now=now) == []


async def test_daily_fires_once_per_day(tmp_path):
    store = ReminderStore(tmp_path / "reminders.db")
    service = ReminderService(store, runtime_events=RuntimeEventBus(path=None))
    service.create(
        title="晨会", schedule_type="daily",
        time_of_day="09:00", timezone="Asia/Shanghai",
    )
    now = datetime(2026, 8, 5, 9, 30, tzinfo=UTC)
    assert len(await service.scan_and_fire(now=now)) == 1
    # 同一天第二次扫描不重复触发
    assert await service.scan_and_fire(now=now + timedelta(hours=2)) == []
    # 次日触发
    assert len(await service.scan_and_fire(now=now + timedelta(days=1))) == 1


async def test_scheduler_starts_and_stops(tmp_path):
    store = ReminderStore(tmp_path / "reminders.db")
    service = ReminderService(store, runtime_events=RuntimeEventBus(path=None))
    scheduler = ReminderScheduler(service, interval_seconds=0.05)
    scheduler.start()
    reminder = service.create(title="t", schedule_type="once", due_at=PAST_DUE)
    await asyncio.sleep(0.25)
    assert store.get(reminder.id).status == "done"
    scheduler.stop()


# --- dismiss / complete ---


def test_dismiss_once_marks_done_and_recurring_skips(tmp_path):
    store = ReminderStore(tmp_path / "reminders.db")
    service = ReminderService(store)
    once = service.create(title="a", schedule_type="once", due_at=PAST_DUE)
    dismissed = service.dismiss(once.id)
    assert dismissed.status == "done"

    daily = service.create(
        title="b", schedule_type="daily",
        time_of_day="09:00", timezone="Asia/Shanghai",
    )
    skipped = service.dismiss(daily.id)
    assert skipped.status == "active"
    assert skipped.last_fired_at is not None
    assert any(e["event_type"] == "dismissed" for e in store.events_for(daily.id))

    assert service.dismiss("missing") is None


def test_complete_marks_done(tmp_path):
    service = make_service(tmp_path)
    reminder = service.create(title="c", schedule_type="once", due_at=PAST_DUE)
    completed = service.complete(reminder.id)
    assert completed.status == "done"
    assert service.complete("missing") is None


# --- API ---


def test_reminders_api_requires_auth(tmp_path):
    client = TestClient(create_app(settings=make_settings(tmp_path)))
    assert client.get("/api/reminders").status_code == 401
    assert client.post("/api/reminders", json={"title": "x", "schedule_type": "once"}).status_code == 401
    assert client.delete("/api/reminders/r1").status_code == 401


def test_reminders_api_crud_flow(tmp_path):
    client = TestClient(create_app(settings=make_settings(tmp_path)))

    created = client.post(
        "/api/reminders",
        headers=AUTH_HEADERS,
        json={
            "title": "喝水",
            "body": "多喝水",
            "schedule_type": "daily",
            "time_of_day": "09:30",
            "timezone": "Asia/Shanghai",
        },
    )
    assert created.status_code == 200
    reminder = created.json()
    assert reminder["title"] == "喝水"
    assert reminder["schedule_type"] == "daily"
    assert reminder["status"] == "active"

    listed = client.get("/api/reminders", headers=AUTH_HEADERS).json()
    assert [item["id"] for item in listed["reminders"]] == [reminder["id"]]

    patched = client.patch(
        f"/api/reminders/{reminder['id']}",
        headers=AUTH_HEADERS,
        json={"title": "喝水x2", "status": "paused"},
    )
    assert patched.json()["title"] == "喝水x2"
    assert patched.json()["status"] == "paused"

    # daily 提醒 dismiss = 跳过本次（不完成），状态保持
    resumed = client.patch(
        f"/api/reminders/{reminder['id']}",
        headers=AUTH_HEADERS,
        json={"status": "active"},
    )
    assert resumed.json()["status"] == "active"
    dismissed = client.post(f"/api/reminders/{reminder['id']}/dismiss", headers=AUTH_HEADERS)
    assert dismissed.json()["status"] == "active"
    assert dismissed.json()["last_fired_at"] is not None

    completed = client.post(f"/api/reminders/{reminder['id']}/complete", headers=AUTH_HEADERS)
    assert completed.json()["status"] == "done"

    deleted = client.delete(f"/api/reminders/{reminder['id']}", headers=AUTH_HEADERS)
    assert deleted.json() == {"deleted": True}

    assert client.patch("/api/reminders/missing", headers=AUTH_HEADERS, json={"title": "x"}).status_code == 404
    assert client.post("/api/reminders/missing/dismiss", headers=AUTH_HEADERS).status_code == 404
    assert client.post("/api/reminders/missing/complete", headers=AUTH_HEADERS).status_code == 404
    assert client.delete("/api/reminders/missing", headers=AUTH_HEADERS).status_code == 404


def test_reminders_api_status_filter_and_validation(tmp_path):
    client = TestClient(create_app(settings=make_settings(tmp_path)))

    assert client.get("/api/reminders?status=bogus", headers=AUTH_HEADERS).status_code == 422

    once_without_due = client.post(
        "/api/reminders",
        headers=AUTH_HEADERS,
        json={"title": "x", "schedule_type": "once"},
    )
    assert once_without_due.status_code == 422
    assert once_without_due.json()["error"]["code"] == "invalid_schedule"

    daily_without_time = client.post(
        "/api/reminders",
        headers=AUTH_HEADERS,
        json={"title": "x", "schedule_type": "daily", "timezone": "UTC"},
    )
    assert daily_without_time.status_code == 422

    bad_schedule = client.post(
        "/api/reminders",
        headers=AUTH_HEADERS,
        json={"title": "x", "schedule_type": "hourly", "due_at": PAST_DUE},
    )
    assert bad_schedule.status_code == 422


def test_runtime_status_reports_due_reminders(tmp_path):
    app = create_app(settings=make_settings(tmp_path))
    client = TestClient(app)
    app.state.reminder_service.create(
        title="t", schedule_type="once", due_at=PAST_DUE
    )
    data = client.get("/api/runtime/status", headers=AUTH_HEADERS).json()
    assert data["due_reminders"] == 1


# --- D2: weekly day_of_week 星期匹配 ---


def test_weekly_day_of_week_matches_only_specified_weekday(tmp_path):
    service = make_service(tmp_path)
    # 每周一 09:00（day_of_week=0）
    reminder = service.create(
        title="周会",
        schedule_type="weekly",
        time_of_day="09:00",
        timezone="Asia/Shanghai",
        day_of_week=0,
    )
    # 2026-08-03 是周一（北京 09:30 已过 09:00）
    monday = datetime(2026, 8, 3, 1, 30, tzinfo=UTC)
    assert monday.astimezone(ZoneInfo("Asia/Shanghai")).weekday() == 0
    assert service.is_due(reminder, monday) is True
    # 2026-08-05 是周三（北京 09:30）：星期不匹配，不触发
    wednesday = datetime(2026, 8, 5, 1, 30, tzinfo=UTC)
    assert wednesday.astimezone(ZoneInfo("Asia/Shanghai")).weekday() == 2
    assert service.is_due(reminder, wednesday) is False


def test_weekly_without_day_of_week_keeps_7day_interval(tmp_path):
    """未设 day_of_week 时保持旧行为：任意星期、7 天间隔。"""
    service = make_service(tmp_path)
    reminder = service.create(
        title="旧周提醒",
        schedule_type="weekly",
        time_of_day="09:00",
        timezone="Asia/Shanghai",
    )
    # 周三 09:30 也触发（旧行为）
    wednesday = datetime(2026, 8, 5, 1, 30, tzinfo=UTC)
    assert service.is_due(reminder, wednesday) is True
    # 触发一次后 7 天内不重复
    reminder.last_fired_at = "2026-08-04T01:30:00+00:00"
    assert service.is_due(reminder, wednesday) is False


def test_day_of_week_validation(tmp_path):
    service = make_service(tmp_path)
    import pytest as _pytest

    with _pytest.raises(ValueError):
        service.create(
            title="bad", schedule_type="weekly", time_of_day="09:00",
            day_of_week=7,  # 越界
        )
    with _pytest.raises(ValueError):
        service.create(
            title="bad", schedule_type="daily", time_of_day="09:00",
            day_of_week=0,  # 非 weekly 不可用
        )
