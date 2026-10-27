"""个人知识库 API：文档上传、列表、集合 CRUD、索引状态与异步重建。"""

import asyncio
import threading
from contextlib import suppress
from uuid import uuid4

from fastapi import APIRouter, Depends, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field

from desktopmate_agent.api.routes import require_auth
from desktopmate_agent.api.schemas import KnowledgeDocumentCreate
from desktopmate_agent.knowledge.cleanup import cleanup_stale
from desktopmate_agent.knowledge.service import DocumentService, DocumentUploadError
from desktopmate_agent.knowledge.store import DocumentStore, KnowledgeCollection, KnowledgeDocumentRecord


class CollectionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=1_000)


class CollectionUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=1_000)


def create_knowledge_router(document_service: DocumentService) -> APIRouter:
    router = APIRouter()
    store = document_service.store
    cleanup_task: asyncio.Task[None] | None = None

    @router.on_event("startup")
    async def start_cleanup_task() -> None:
        nonlocal cleanup_task
        cleanup_task = asyncio.create_task(run_daily_cleanup(document_service))

    @router.on_event("shutdown")
    async def stop_cleanup_task() -> None:
        if cleanup_task is None:
            return
        cleanup_task.cancel()
        with suppress(asyncio.CancelledError):
            await cleanup_task

    # --- Documents ---

    @router.post("/api/knowledge/documents")
    async def upload_document(
        file: UploadFile,
        collection_id: str | None = Form(default=None),
        importance: float = Form(default=0.5, ge=0.0, le=1.0),
        _: None = Depends(require_auth),
    ) -> KnowledgeDocumentRecord:
        payload = KnowledgeDocumentCreate(collection_id=collection_id, importance=importance)
        content = await file.read()
        try:
            return await asyncio.to_thread(
                document_service.upload,
                filename=file.filename or "unnamed",
                content=content,
                collection_id=payload.collection_id,
                importance=payload.importance,
            )
        except DocumentUploadError as exc:
            raise HTTPException(
                status_code=400,
                detail={"code": "invalid_document", "message": str(exc)},
            ) from exc

    @router.get("/api/knowledge/documents")
    def list_documents(
        collection: str | None = Query(default=None),
        status: str | None = Query(default=None),
        q: str | None = Query(default=None, max_length=200),
        _: None = Depends(require_auth),
    ) -> dict[str, object]:
        documents = store.list_documents(collection_id=collection, status=status, query=q)
        return {"documents": documents}

    @router.get("/api/knowledge/documents/{document_id}")
    def get_document(
        document_id: str,
        _: None = Depends(require_auth),
    ) -> KnowledgeDocumentRecord:
        return _require_document(store, document_id)

    @router.delete("/api/knowledge/documents/{document_id}")
    def delete_document(
        document_id: str,
        _: None = Depends(require_auth),
    ) -> dict[str, bool]:
        if not document_service.delete(document_id):
            raise HTTPException(
                status_code=404,
                detail={"code": "document_not_found", "message": f"文档 {document_id} 不存在"},
            )
        return {"deleted": True}

    @router.post("/api/knowledge/documents/{document_id}/reindex")
    def reindex_document(
        document_id: str,
        _: None = Depends(require_auth),
    ) -> KnowledgeDocumentRecord:
        try:
            return document_service.reindex(document_id)
        except KeyError as exc:
            raise HTTPException(
                status_code=404,
                detail={"code": "document_not_found", "message": str(exc)},
            ) from exc

    @router.post("/api/knowledge/refresh")
    def refresh_knowledge(_: None = Depends(require_auth)) -> dict[str, object]:
        return document_service.refresh()

    # --- Collections ---

    @router.get("/api/knowledge/collections")
    def list_collections(_: None = Depends(require_auth)) -> dict[str, object]:
        return {"collections": store.list_collections()}

    @router.post("/api/knowledge/collections")
    def create_collection(
        payload: CollectionCreate,
        _: None = Depends(require_auth),
    ) -> KnowledgeCollection:
        return store.create_collection(name=payload.name, description=payload.description)

    @router.patch("/api/knowledge/collections/{collection_id}")
    def update_collection(
        collection_id: str,
        payload: CollectionUpdate,
        _: None = Depends(require_auth),
    ) -> KnowledgeCollection:
        updated = store.update_collection(
            collection_id,
            name=payload.name,
            description=payload.description,
        )
        if updated is None:
            raise HTTPException(
                status_code=404,
                detail={"code": "collection_not_found", "message": f"集合 {collection_id} 不存在"},
            )
        return updated

    @router.delete("/api/knowledge/collections/{collection_id}")
    def delete_collection(
        collection_id: str,
        _: None = Depends(require_auth),
    ) -> dict[str, bool]:
        if not store.delete_collection(collection_id):
            raise HTTPException(
                status_code=404,
                detail={"code": "collection_not_found", "message": f"集合 {collection_id} 不存在"},
            )
        return {"deleted": True}

    # --- S7: 索引状态与异步重建 ---

    jobs: dict[str, dict[str, object]] = {}
    jobs_lock = threading.Lock()

    def _next_job_id() -> str:
        return f"idx_{uuid4().hex[:12]}"

    def _run_rebuild_job(job_id: str) -> None:
        def on_progress(done: int, total: int) -> None:
            with jobs_lock:
                jobs[job_id]["progress"] = done
                jobs[job_id]["total"] = total

        try:
            result = document_service.rebuild_index(on_progress=on_progress)
            with jobs_lock:
                jobs[job_id].update({"status": "completed", **result})
        except Exception as exc:  # noqa: BLE001
            with jobs_lock:
                jobs[job_id].update({"status": "failed", "error": str(exc)})

    @router.get("/api/knowledge/index/status")
    def index_status(_: None = Depends(require_auth)) -> dict[str, object]:
        meta = store.get_index_meta()
        return {
            "documents": store.count_documents(),
            "chunks": store.count_chunks(),
            "embedding_provider": meta.get("embedding_provider", "none"),
            "embedding_id": meta.get("embedding_id", ""),
            "vector_store": meta.get("vector_store", "memory"),
            "chunk_strategy": meta.get("chunk_strategy", "markdown/500/80"),
            "last_refresh_at": meta.get("last_refresh_at"),
        }

    @router.post("/api/knowledge/index/jobs")
    def start_index_job(_: None = Depends(require_auth)) -> dict[str, str]:
        job_id = _next_job_id()
        with jobs_lock:
            jobs[job_id] = {"status": "queued", "progress": 0, "total": 0}
        threading.Thread(target=_run_rebuild_job, args=(job_id,), daemon=True).start()
        return {"job_id": job_id, "status": "queued"}

    @router.get("/api/knowledge/index/jobs/{job_id}")
    def get_index_job(job_id: str, _: None = Depends(require_auth)) -> dict[str, object]:
        with jobs_lock:
            job = dict(jobs.get(job_id, {}))
        if not job:
            raise HTTPException(
                status_code=404,
                detail={"code": "job_not_found", "message": f"索引任务 {job_id} 不存在"},
            )
        return job

    return router


async def run_daily_cleanup(document_service: DocumentService) -> None:
    while True:
        await asyncio.to_thread(cleanup_stale, document_service.store)
        document_service.search_tool.refresh_index()
        await asyncio.sleep(24 * 60 * 60)


def _require_document(
    store: DocumentStore,
    document_id: str,
) -> KnowledgeDocumentRecord:
    document = store.get_document(document_id)
    if document is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "document_not_found", "message": f"文档 {document_id} 不存在"},
        )
    return document
