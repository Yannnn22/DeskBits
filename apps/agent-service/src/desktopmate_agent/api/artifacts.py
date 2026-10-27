"""Workspace artifact API."""

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from desktopmate_agent.api.errors import ApiError
from desktopmate_agent.api.routes import require_auth
from desktopmate_agent.artifacts.store import ArtifactStore


class ArtifactCreate(BaseModel):
    kind: str = Field(default="document", min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(default="", max_length=500_000)
    source_session_id: str | None = Field(default=None, max_length=128)
    external_ref: str | None = Field(default=None, max_length=200)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ArtifactRevision(BaseModel):
    content: str = Field(min_length=1, max_length=500_000)
    title: str | None = Field(default=None, min_length=1, max_length=200)
    metadata: dict[str, Any] = Field(default_factory=dict)


def create_artifacts_router(store: ArtifactStore) -> APIRouter:
    router = APIRouter(prefix="/api/artifacts", tags=["artifacts"], dependencies=[Depends(require_auth)])

    @router.get("")
    def list_artifacts(include_drafts: bool = False) -> dict[str, object]:
        return {"artifacts": [item.to_dict() for item in store.list(include_drafts=include_drafts)]}

    @router.post("")
    def create_artifact(payload: ArtifactCreate) -> dict[str, object]:
        record = store.create_draft(
            kind=payload.kind,
            title=payload.title,
            content=payload.content,
            source_session_id=payload.source_session_id,
            external_ref=payload.external_ref,
            metadata=payload.metadata,
        )
        return record.to_dict()

    @router.get("/{artifact_id}")
    def get_artifact(artifact_id: str) -> dict[str, object]:
        record = store.get(artifact_id)
        if record is None or record.state == "archived":
            raise ApiError(status_code=404, code="artifact_not_found", message="工作区成品不存在")
        return record.to_dict()

    @router.post("/{artifact_id}/confirm")
    def confirm_artifact(artifact_id: str) -> dict[str, object]:
        record = store.confirm(artifact_id)
        if record is None:
            raise ApiError(status_code=409, code="artifact_not_confirmable", message="没有可确认的候选版本")
        return record.to_dict()

    @router.post("/{artifact_id}/revisions")
    def create_revision(artifact_id: str, payload: ArtifactRevision) -> dict[str, object]:
        record = store.revise(
            artifact_id,
            content=payload.content,
            title=payload.title,
            metadata=payload.metadata,
        )
        if record is None:
            raise ApiError(status_code=404, code="artifact_not_found", message="工作区成品不存在")
        # 这条响应由发起修改的当前对话消费，应返回待确认版本；后续工作区读取
        # 仍会默认拿到已确认的 current 版本。
        return record.to_dict(include_pending=True)

    @router.delete("/{artifact_id}")
    def archive_artifact(artifact_id: str) -> dict[str, bool]:
        if not store.archive(artifact_id):
            raise ApiError(status_code=404, code="artifact_not_found", message="工作区成品不存在")
        return {"archived": True}

    return router
