"""图片附件 API：上传与鉴权读取。"""

from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from desktopmate_agent.api.routes import _bearer_token, require_auth
from desktopmate_agent.attachments.store import (
    ALLOWED_DOCUMENT_TYPES,
    ALLOWED_IMAGE_TYPES,
    AttachmentStore,
)


class AttachmentItem(BaseModel):
    id: str
    filename: str
    mime: str
    size: int


class AttachmentLocation(BaseModel):
    path: str


class UploadResponse(BaseModel):
    attachments: list[AttachmentItem]


def create_attachment_router(store: AttachmentStore) -> APIRouter:
    router = APIRouter()

    @router.post("/api/uploads")
    async def upload_images(
        files: list[UploadFile],
        _: None = Depends(require_auth),
    ) -> UploadResponse:
        if not files:
            raise HTTPException(
                status_code=400,
                detail=dict(code="no_files", message="没有收到附件文件"),
            )
        items: list[AttachmentItem] = []
        for file in files:
            mime = file.content_type or ""
            if mime not in ALLOWED_IMAGE_TYPES and mime not in ALLOWED_DOCUMENT_TYPES:
                raise HTTPException(
                    status_code=400,
                    detail={
                        "code": "unsupported_attachment_type",
                        "message": (
                            f"不支持的附件类型: {mime or '未知'}"
                            "（仅支持图片 PNG/JPEG/WebP/GIF 和文档 md/txt/pdf/docx）"
                        ),
                    },
                )
            content = await file.read()
            try:
                record = store.save(
                    filename=file.filename or "attachment",
                    mime=mime,
                    content=content,
                )
            except Exception as exc:  # ApiError from store
                raise HTTPException(
                    status_code=400,
                    detail={"code": "upload_failed", "message": str(exc)},
                ) from exc
            items.append(
                AttachmentItem(
                    id=record.id,
                    filename=record.filename,
                    mime=record.mime,
                    size=record.size,
                )
            )
        return UploadResponse(attachments=items)

    @router.get("/api/attachments/{attachment_id}")
    def get_attachment(
        attachment_id: str,
        request: Request,
        token: str | None = Query(default=None),
    ) -> FileResponse:
        # 图片 <img> 标签无法携带 header，允许 query token（与 WebSocket 一致）
        expected = request.app.state.auth_token
        provided = token or request.headers.get("X-DesktopMate-Token", "") or _bearer_token(
            request.headers.get("Authorization", "")
        )
        if not expected or not provided or not secrets.compare_digest(provided, expected):
            raise HTTPException(status_code=401, detail={"code": "unauthorized", "message": "Unauthorized"})
        record = store.get(attachment_id)
        path = store.path_for(attachment_id)
        if record is None or path is None:
            raise HTTPException(
                status_code=404,
                detail={"code": "attachment_not_found", "message": "附件不存在"},
            )
        return FileResponse(
            path,
            media_type=record.mime,
            filename=record.filename,
            content_disposition_type="inline",
        )

    @router.get("/api/attachments/{attachment_id}/location")
    def get_attachment_location(
        attachment_id: str,
        _: None = Depends(require_auth),
    ) -> AttachmentLocation:
        path = store.path_for(attachment_id)
        if path is None:
            raise HTTPException(
                status_code=404,
                detail={"code": "attachment_not_found", "message": "附件不存在"},
            )
        return AttachmentLocation(path=str(path))

    return router
