"""待办与提醒 API：CRUD + dismiss / complete / 软删。"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from desktopmate_agent.api.errors import ApiError
from desktopmate_agent.api.routes import require_auth
from desktopmate_agent.reminders.service import ReminderService
from desktopmate_agent.reminders.store import (
    Reminder,
    ReminderScheduleType,
    ReminderStatus,
    VALID_SCHEDULE_TYPES,
    VALID_STATUSES,
)


class ReminderCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    body: str | None = Field(default=None, max_length=2000)
    schedule_type: ReminderScheduleType
    due_at: str | None = Field(default=None, max_length=64)
    time_of_day: str | None = Field(default=None, max_length=8)
    day_of_week: int | None = Field(default=None, ge=0, le=6)
    timezone: str = Field(default="UTC", max_length=64)


class ReminderPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    body: str | None = Field(default=None, max_length=2000)
    schedule_type: ReminderScheduleType | None = None
    due_at: str | None = Field(default=None, max_length=64)
    time_of_day: str | None = Field(default=None, max_length=8)
    day_of_week: int | None = Field(default=None, ge=0, le=6)
    timezone: str | None = Field(default=None, max_length=64)
    status: ReminderStatus | None = None


def create_reminders_router(service: ReminderService) -> APIRouter:
    router = APIRouter(dependencies=[Depends(require_auth)])

    @router.get("/api/reminders")
    def list_reminders(status: str = "active") -> dict[str, object]:
        if status not in VALID_STATUSES | {"all"}:
            raise ApiError(
                code="invalid_status",
                message=f"status 必须是 active|paused|done|all，收到 {status!r}",
                status_code=422,
            )
        return {"reminders": service.store.list(status=status)}

    @router.post("/api/reminders")
    def create_reminder(payload: ReminderCreate) -> Reminder:
        try:
            return service.create(
                title=payload.title,
                body=payload.body,
                schedule_type=payload.schedule_type,
                due_at=payload.due_at,
                time_of_day=payload.time_of_day,
                day_of_week=payload.day_of_week,
                timezone=payload.timezone or "UTC",
            )
        except ValueError as exc:
            raise ApiError(code="invalid_schedule", message=str(exc), status_code=422) from exc

    @router.patch("/api/reminders/{reminder_id}")
    def patch_reminder(reminder_id: str, payload: ReminderPatch) -> Reminder:
        patch = payload.model_dump(exclude_unset=True)
        if payload.schedule_type is not None and payload.schedule_type not in VALID_SCHEDULE_TYPES:
            raise ApiError(
                code="invalid_schedule",
                message=f"无效的 schedule_type: {payload.schedule_type!r}",
                status_code=422,
            )
        try:
            updated = service.update(reminder_id, patch)
        except ValueError as exc:
            raise ApiError(code="invalid_schedule", message=str(exc), status_code=422) from exc
        if updated is None:
            raise ApiError(code="reminder_not_found", message="提醒不存在", status_code=404)
        return updated

    @router.post("/api/reminders/{reminder_id}/dismiss")
    def dismiss_reminder(reminder_id: str) -> Reminder:
        updated = service.dismiss(reminder_id)
        if updated is None:
            raise ApiError(code="reminder_not_found", message="提醒不存在", status_code=404)
        return updated

    @router.post("/api/reminders/{reminder_id}/complete")
    def complete_reminder(reminder_id: str) -> Reminder:
        updated = service.complete(reminder_id)
        if updated is None:
            raise ApiError(code="reminder_not_found", message="提醒不存在", status_code=404)
        return updated

    @router.delete("/api/reminders/{reminder_id}")
    def delete_reminder(reminder_id: str) -> dict[str, bool]:
        if not service.delete(reminder_id):
            raise ApiError(code="reminder_not_found", message="提醒不存在", status_code=404)
        return {"deleted": True}

    return router
