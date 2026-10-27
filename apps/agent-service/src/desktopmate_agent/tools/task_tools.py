"""Task and reminder tools exposed to the agent graph."""

from pydantic import BaseModel, Field

from desktopmate_agent.memory.models import MemoryEntry, MemoryScope
from desktopmate_agent.memory.store import JsonlMemoryStore, RedisMemoryStore, SqliteMemoryStore
from desktopmate_agent.reminders.service import ReminderService
from desktopmate_agent.reminders.store import ReminderScheduleType
from desktopmate_agent.tasks.store import TaskStore, VALID_STATUSES
from desktopmate_agent.tools.base import BaseTool, RiskLevel, ToolResult


class TaskCreateArgs(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)
    due_at: str = Field(default="", max_length=64)
    category: str = Field(default="个人", max_length=40)
    priority: str = Field(default="normal", max_length=12)
    next_action: str = Field(default="", max_length=200)


class TaskUpdateArgs(BaseModel):
    task_id: str = Field(min_length=1, max_length=128)
    title: str = Field(default="", max_length=200)
    description: str = Field(default="", max_length=2000)
    due_at: str = Field(default="", max_length=64)
    status: str = Field(default="", max_length=20)
    category: str = Field(default="", max_length=40)
    priority: str = Field(default="", max_length=12)
    next_action: str = Field(default="", max_length=200)


class TaskListArgs(BaseModel):
    status: str = Field(default="", max_length=20)


class ReminderCreateArgs(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(default="", max_length=2000)
    schedule_type: ReminderScheduleType = "once"
    due_at: str = Field(default="", max_length=64)
    time_of_day: str = Field(default="", max_length=8)
    # weekly 专用：0=周一 .. 6=周日（如"每周一 9:00" → day_of_week=0, time_of_day="09:00"）
    day_of_week: int | None = Field(default=None, ge=0, le=6)
    timezone: str = Field(default="Asia/Shanghai", max_length=64)


class TaskDraftArgs(BaseModel):
    """A proposed todo, intentionally kept outside persistent task storage."""

    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)
    due_at: str = Field(default="", max_length=64)
    timezone: str = Field(default="Asia/Shanghai", max_length=64)
    create_reminder: bool = False
    category: str = Field(default="个人", max_length=40)
    priority: str = Field(default="normal", max_length=12)
    next_action: str = Field(default="", max_length=200)
    reminder_message: str = Field(default="", max_length=300)
    memory_summary: str = Field(default="", max_length=500)


class TaskDraftTool(BaseTool[TaskDraftArgs]):
    """Build a reviewable task candidate before the user decides to save it."""

    name = "task_draft"
    description = "Prepare a todo candidate with an optional one-time reminder; does not save anything."
    args_schema = TaskDraftArgs
    risk_level = RiskLevel.READ
    requires_permission = False

    async def run(self, args: TaskDraftArgs) -> ToolResult:
        due_at = args.due_at or None
        priority = args.priority if args.priority in {"low", "normal", "high"} else "normal"
        reminder = (
            {
                "schedule_type": "once",
                "due_at": due_at,
                "timezone": args.timezone,
                "message": args.reminder_message or f"提醒你：{args.title}",
            }
            if due_at and args.create_reminder
            else None
        )
        return ToolResult(
            status="success",
            summary=f"已生成待办候选：{args.title}（确认保存后加入工作区）",
            data={
                "task": {
                    "title": args.title,
                    "description": args.description,
                    "due_at": due_at,
                    "status": "todo",
                    "category": args.category,
                    "priority": priority,
                    "next_action": args.next_action,
                    "memory_summary": args.memory_summary,
                },
                "reminder": reminder,
            },
        )


class TaskConfirmArgs(TaskDraftArgs):
    """Explicit user confirmation for a previously reviewed task candidate."""

    session_id: str = Field(min_length=1, max_length=128)


class TaskConfirmTool(BaseTool[TaskConfirmArgs]):
    """Persist a confirmed task, its optional reminder, and session memory together."""

    name = "task_confirm"
    description = "Save an explicitly confirmed task candidate and its reminder."
    args_schema = TaskConfirmArgs
    risk_level = RiskLevel.WRITE
    requires_permission = False
    agent_visible = False

    def __init__(
        self,
        *,
        store: TaskStore,
        reminder_service: ReminderService,
        memory_store: JsonlMemoryStore | RedisMemoryStore | SqliteMemoryStore | None,
    ) -> None:
        self.store = store
        self.reminder_service = reminder_service
        self.memory_store = memory_store

    async def run(self, args: TaskConfirmArgs) -> ToolResult:
        task = self.store.create(
            title=args.title,
            description=args.description or None,
            due_at=args.due_at or None,
            category=args.category,
            priority=args.priority if args.priority in {"low", "normal", "high"} else "normal",
            next_action=args.next_action or None,
            source_session_id=args.session_id,
        )
        reminder = None
        try:
            if args.create_reminder and args.due_at:
                reminder = self.reminder_service.create(
                    title=args.title,
                    body=args.reminder_message or args.description or None,
                    schedule_type="once",
                    due_at=args.due_at,
                    timezone=args.timezone,
                )
                task = self.store.update(task.id, reminder_id=reminder.id) or task
        except Exception as exc:
            if reminder is not None:
                self.reminder_service.delete(reminder.id)
            self.store.delete(task.id)
            return ToolResult(
                status="error",
                summary=f"保存待办失败：{exc}",
                data={"error": str(exc)},
            )

        memory = None
        if self.memory_store is not None:
            memory_value = args.memory_summary or (
                f"待办「{args.title}」：{args.description or '待处理'}"
            )
            entry = MemoryEntry(
                scope=MemoryScope.SESSION,
                subject_id=args.session_id,
                session_id=args.session_id,
                key=f"task:{task.id}",
                value=memory_value,
                source="task_confirmed",
            )
            try:
                self.memory_store.upsert(entry)
                memory = {"scope": entry.scope.value, "key": entry.key, "value": entry.value}
            except Exception as exc:  # noqa: BLE001
                # The task/reminder are already committed. Surface the degraded
                # memory state without falsely reporting the saved task as failed.
                memory = {"scope": entry.scope.value, "key": entry.key, "value": ""}
                memory["error"] = str(exc)

        return ToolResult(
            status="success",
            summary=f"已保存待办：{task.title}",
            data={
                "task": task.to_dict(),
                "reminder": reminder.model_dump() if reminder is not None else None,
                "memory": memory,
            },
        )
class TaskCreateTool(BaseTool[TaskCreateArgs]):
    name = "task_create"
    description = "Create a local todo/task with title, optional description, and optional due_at."
    args_schema = TaskCreateArgs
    risk_level = RiskLevel.WRITE
    requires_permission = False
    agent_visible = False

    def __init__(self, *, store: TaskStore) -> None:
        self.store = store

    async def run(self, args: TaskCreateArgs) -> ToolResult:
        task = self.store.create(
            title=args.title,
            description=args.description or None,
            due_at=args.due_at or None,
            category=args.category,
            priority=args.priority if args.priority in {"low", "normal", "high"} else "normal",
            next_action=args.next_action or None,
        )
        return ToolResult(
            status="success",
            summary=f"已创建待办：{task.title}",
            data={"task": task.to_dict()},
        )


class TaskUpdateTool(BaseTool[TaskUpdateArgs]):
    name = "task_update"
    description = "Update a local task title, description, due_at, or status (todo, in_progress, done)."
    args_schema = TaskUpdateArgs
    risk_level = RiskLevel.WRITE
    requires_permission = False
    agent_visible = False

    def __init__(self, *, store: TaskStore) -> None:
        self.store = store

    async def run(self, args: TaskUpdateArgs) -> ToolResult:
        if args.status and args.status not in VALID_STATUSES:
            return ToolResult(
                status="error",
                summary=f"无效任务状态：{args.status}",
                data={"valid_statuses": list(VALID_STATUSES)},
            )
        patch = {
            key: value
            for key, value in {
                "title": args.title,
                "description": args.description,
                "due_at": args.due_at,
                "status": args.status,
                "category": args.category,
                "priority": args.priority if args.priority in {"low", "normal", "high"} else "",
                "next_action": args.next_action,
            }.items()
            if value
        }
        task = self.store.update(args.task_id, **patch)
        if task is None:
            return ToolResult(
                status="error",
                summary="任务不存在",
                data={"task_id": args.task_id},
            )
        return ToolResult(
            status="success",
            summary=f"已更新待办：{task.title}",
            data={"task": task.to_dict()},
        )


class TaskListTool(BaseTool[TaskListArgs]):
    name = "task_list"
    description = "List local tasks, optionally filtered by status: todo, in_progress, done."
    args_schema = TaskListArgs
    risk_level = RiskLevel.READ
    requires_permission = False

    def __init__(self, *, store: TaskStore) -> None:
        self.store = store

    async def run(self, args: TaskListArgs) -> ToolResult:
        status = args.status or None
        if status and status not in VALID_STATUSES:
            return ToolResult(
                status="error",
                summary=f"无效任务状态：{status}",
                data={"valid_statuses": list(VALID_STATUSES)},
            )
        tasks = [task.to_dict() for task in self.store.list(status=status)]
        return ToolResult(
            status="success",
            summary=f"找到 {len(tasks)} 项待办",
            data={"tasks": tasks},
        )


class ReminderCreateTool(BaseTool[ReminderCreateArgs]):
    name = "reminder_create"
    description = (
        "Create a local reminder. once requires due_at; daily uses time_of_day; "
        "weekly uses time_of_day plus day_of_week (0=Mon..6=Sun) for 'every Monday' style."
    )
    args_schema = ReminderCreateArgs
    risk_level = RiskLevel.WRITE
    requires_permission = False
    agent_visible = False

    def __init__(self, *, service: ReminderService) -> None:
        self.service = service

    async def run(self, args: ReminderCreateArgs) -> ToolResult:
        try:
            reminder = self.service.create(
                title=args.title,
                body=args.body or None,
                schedule_type=args.schedule_type,
                due_at=args.due_at or None,
                time_of_day=args.time_of_day or None,
                day_of_week=args.day_of_week,
                timezone=args.timezone or "Asia/Shanghai",
            )
        except ValueError as exc:
            return ToolResult(
                status="error",
                summary=f"创建提醒失败：{exc}",
                data={"error": str(exc)},
            )
        return ToolResult(
            status="success",
            summary=f"已创建提醒：{reminder.title}",
            data={"reminder": reminder.model_dump()},
        )
