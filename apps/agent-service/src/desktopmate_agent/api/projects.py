"""项目 API：左侧栏项目列表、项目内对话、项目上下文。

- GET    /api/projects              → 项目列表
- POST   /api/projects              → 添加项目（name + path，路径须在 allowed_roots 内）
- DELETE /api/projects/{id}         → 移除项目（仅删引用，不删文件）
- GET    /api/projects/{id}/sessions→ 项目下的会话列表
- POST   /api/projects/{id}/sessions→ 在项目下创建新会话
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from desktopmate_agent.api.routes import require_auth
from desktopmate_agent.projects.store import ProjectRecord, ProjectStore
from desktopmate_agent.sessions.store import SessionStore


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    path: str = Field(min_length=1, max_length=2000)


class ProjectCreateResponse(BaseModel):
    project: ProjectRecord


class ProjectListResponse(BaseModel):
    projects: list[ProjectRecord]


def _resolve_project_path(path: str, allowed_roots: list[Path]) -> Path:
    """校验项目路径在 allowed_roots 内且是目录。"""
    resolved = Path(path).expanduser().resolve()
    roots = [root.resolve() for root in allowed_roots]
    if not any(resolved == root or root in resolved.parents for root in roots):
        raise HTTPException(
            status_code=403,
            detail=dict(code="path_outside_roots", message="项目路径不在可访问范围内"),
        )
    if not resolved.is_dir():
        raise HTTPException(
            status_code=404,
            detail=dict(code="not_a_directory", message="项目路径不是有效目录"),
        )
    return resolved


def create_projects_router(
    project_store: ProjectStore,
    session_store: SessionStore,
    allowed_roots: list[Path],
) -> APIRouter:
    router = APIRouter()

    @router.get("/api/projects")
    def list_projects(_: None = Depends(require_auth)) -> dict[str, object]:
        projects = project_store.list_projects()
        items = []
        for project in projects:
            path = Path(project.path)
            items.append(
                {
                    **project.model_dump(),
                    "exists": path.exists(),
                }
            )
        return {"projects": items}

    @router.post("/api/projects")
    def add_project(
        payload: ProjectCreate,
        _: None = Depends(require_auth),
    ) -> dict[str, object]:
        resolved = _resolve_project_path(payload.path, allowed_roots)
        existing = project_store.get_by_path(str(resolved))
        if existing is not None:
            raise HTTPException(
                status_code=409,
                detail=dict(code="project_exists", message="该项目已添加"),
            )
        project = project_store.add(name=payload.name, path=str(resolved))
        return {"project": project.model_dump()}

    @router.delete("/api/projects/{project_id}")
    def remove_project(
        project_id: str,
        _: None = Depends(require_auth),
    ) -> dict[str, bool]:
        if not project_store.remove(project_id):
            raise HTTPException(
                status_code=404,
                detail=dict(code="project_not_found", message="项目不存在"),
            )
        return {"deleted": True}

    @router.get("/api/projects/{project_id}/sessions")
    def list_project_sessions(
        project_id: str,
        _: None = Depends(require_auth),
    ) -> dict[str, object]:
        if project_store.get(project_id) is None:
            raise HTTPException(
                status_code=404,
                detail=dict(code="project_not_found", message="项目不存在"),
            )
        sessions = session_store.list_sessions_by_project(project_id)
        return {"sessions": [session.model_dump() for session in sessions]}

    @router.post("/api/projects/{project_id}/sessions")
    def create_project_session(
        project_id: str,
        _: None = Depends(require_auth),
    ) -> dict[str, object]:
        project = project_store.get(project_id)
        if project is None:
            raise HTTPException(
                status_code=404,
                detail=dict(code="project_not_found", message="项目不存在"),
            )
        session = session_store.create_session(
            title=f"{project.name} 项目对话",
            mode="agent",
            project_id=project_id,
        )
        return {"session": session.model_dump()}

    return router
