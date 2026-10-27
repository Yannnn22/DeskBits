"""任务 API：task/todo 统一模型（与前端冻结契约一致）。"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from desktopmate_agent.api.errors import ApiError
from desktopmate_agent.api.routes import require_auth
from desktopmate_agent.tasks.store import TaskStore, VALID_STATUSES
from desktopmate_agent.tools.task_tools import TaskConfirmArgs, TaskConfirmTool


class TaskCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    due_at: str | None = Field(default=None, max_length=64)
    reminder_id: str | None = Field(default=None, max_length=128)
    category: str = Field(default="个人", max_length=40)
    priority: str = Field(default="normal", max_length=12)
    next_action: str | None = Field(default=None, max_length=200)
    source_session_id: str | None = Field(default=None, max_length=128)


class TaskUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    due_at: str | None = Field(default=None, max_length=64)
    reminder_id: str | None = Field(default=None, max_length=128)
    status: str | None = Field(default=None, max_length=20)
    category: str | None = Field(default=None, max_length=40)
    priority: str | None = Field(default=None, max_length=12)
    next_action: str | None = Field(default=None, max_length=200)
    source_session_id: str | None = Field(default=None, max_length=128)


class TaskDraftConfirmation(TaskConfirmArgs):
    pass


def create_tasks_router(store: TaskStore, confirmation_tool: TaskConfirmTool | None = None) -> APIRouter:
    router = APIRouter(prefix="/api/tasks", tags=["tasks"], dependencies=[Depends(require_auth)])

    @router.get("")
    def list_tasks(status: str | None = None) -> dict:
        if status and status not in VALID_STATUSES:
            raise ApiError(status_code=422, code="invalid_status", message=f"status 必须是 {VALID_STATUSES}")
        return {"tasks": [task.to_dict() for task in store.list(status=status)]}

    @router.post("")
    def create_task(body: TaskCreate) -> dict:
        task = store.create(
            title=body.title,
            description=body.description,
            due_at=body.due_at,
            reminder_id=body.reminder_id,
            category=body.category,
            priority=body.priority if body.priority in {"low", "normal", "high"} else "normal",
            next_action=body.next_action,
            source_session_id=body.source_session_id,
        )
        return task.to_dict()

    @router.post("/confirm-draft")
    async def confirm_draft(body: TaskDraftConfirmation) -> dict:
        if confirmation_tool is None:
            raise ApiError(code="task_confirmation_unavailable", message="待办确认服务不可用", status_code=503)
        result = await confirmation_tool.run(TaskConfirmArgs.model_validate(body.model_dump()))
        if result.status == "error":
            raise ApiError(
                code="task_confirmation_failed",
                message=result.summary,
                status_code=422,
            )
        return result.data

    @router.patch("/{task_id}")
    def update_task(task_id: str, body: TaskUpdate) -> dict:
        if body.status and body.status not in VALID_STATUSES:
            raise ApiError(status_code=422, code="invalid_status", message=f"status 必须是 {VALID_STATUSES}")
        patch = body.model_dump(exclude_unset=True)
        if "priority" in patch and patch["priority"] not in {"low", "normal", "high"}:
            patch["priority"] = "normal"
        task = store.update(task_id, **patch)
        if task is None:
            raise ApiError(status_code=404, code="task_not_found", message="任务不存在")
        return task.to_dict()

    @router.delete("/{task_id}")
    def delete_task(task_id: str) -> dict:
        if not store.delete(task_id):
            raise ApiError(status_code=404, code="task_not_found", message="任务不存在")
        return {"deleted": True}

    return router
