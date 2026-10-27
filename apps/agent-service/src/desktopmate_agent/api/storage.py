"""S6 存储统计与清理：usage + cleanup API。"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from desktopmate_agent.api.errors import ApiError
from desktopmate_agent.api.routes import require_auth
from desktopmate_agent.storage.service import StorageService


class CleanupRequest(BaseModel):
    targets: list[str] = Field(min_length=1)
    older_than_days: int = Field(default=30, ge=1, le=3650)


def create_storage_router(service: StorageService) -> APIRouter:
    router = APIRouter(dependencies=[Depends(require_auth)])

    @router.get("/api/storage/usage")
    def storage_usage() -> dict[str, object]:
        return service.usage()

    @router.post("/api/storage/cleanup")
    async def storage_cleanup(payload: CleanupRequest) -> dict[str, object]:
        try:
            return await service.cleanup(
                targets=payload.targets,
                older_than_days=payload.older_than_days,
            )
        except ValueError as exc:
            raise ApiError(code="invalid_target", message=str(exc), status_code=422) from exc

    return router
