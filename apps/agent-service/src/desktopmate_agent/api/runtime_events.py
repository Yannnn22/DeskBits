"""Runtime 事件 API：桌宠当前状态 + 冷启动事件恢复。

- ``GET /api/runtime/status``：active_runs / last_run_status / last_message /
  pending_permissions / due_reminders。
- ``GET /api/runtime/events?since=<cursor>&limit=``：增量拉取事件，返回
  ``{"events": [...], "next_cursor": "evt_..."}``，cursor 支持整数 seq 或
  ``evt_<seq>`` 两种形式。
"""

from fastapi import APIRouter, Depends, Request

from desktopmate_agent.api.routes import require_auth
from desktopmate_agent.reminders.service import ReminderService
from desktopmate_agent.runtime.events import RuntimeEventBus, _seq_of_id
from desktopmate_agent.runtime.runs import RunRegistry
from desktopmate_agent.tools.permissions import PermissionRequestBroker


def create_runtime_router(
    bus: RuntimeEventBus,
    run_registry: RunRegistry,
    permission_broker: PermissionRequestBroker,
    reminder_service: ReminderService | None = None,
) -> APIRouter:
    router = APIRouter(dependencies=[Depends(require_auth)])

    @router.get("/api/runtime/status")
    def runtime_status(request: Request) -> dict[str, object]:
        return bus.snapshot(
            active_runs=len(run_registry.active_run_ids()),
            pending_permissions=permission_broker.pending_count(),
            due_reminders=reminder_service.due_reminders_count() if reminder_service is not None else 0,
        )

    @router.get("/api/runtime/events")
    def runtime_events(request: Request, since: str = "0", limit: int = 100) -> dict[str, object]:
        cursor = _parse_cursor(since)
        items, latest_seq = bus.events_since(cursor, limit=limit)
        return {
            "events": items,
            "next_cursor": f"evt_{latest_seq:08d}",
        }

    return router


def _parse_cursor(raw: str) -> int:
    if raw is None or raw == "":
        return 0
    if raw.isdigit():
        return int(raw)
    return _seq_of_id(raw)
