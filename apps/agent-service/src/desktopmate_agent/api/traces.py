"""Trace / 可观测性 API：查看 run 事件流与会话 runs。"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from desktopmate_agent.api.routes import require_auth
from desktopmate_agent.trace.store import JsonlTraceStore


def create_trace_router(trace_store: JsonlTraceStore) -> APIRouter:
    router = APIRouter()

    @router.get("/api/runs/{run_id}/trace")
    def run_trace(
        run_id: str,
        _: None = Depends(require_auth),
    ) -> dict[str, object]:
        if not trace_store.has_run(run_id):
            raise HTTPException(
                status_code=404,
                detail={"code": "run_not_found", "message": f"运行 {run_id} 不存在"},
            )
        events = trace_store.read_run(run_id)
        return {"run_id": run_id, "events": [_event_response(run_id, index, event) for index, event in enumerate(events)]}

    @router.get("/api/sessions/{session_id}/runs")
    def session_runs(
        session_id: str,
        _: None = Depends(require_auth),
    ) -> dict[str, object]:
        return {"session_id": session_id, "runs": trace_store.list_run_ids(session_id)}

    @router.get("/api/events")
    def query_events(
        session_id: str | None = Query(default=None, max_length=128),
        run_id: str | None = Query(default=None, max_length=128),
        type: str | None = Query(default=None, alias="type", max_length=64),
        _: None = Depends(require_auth),
    ) -> dict[str, object]:
        events = trace_store.query_events(
            session_id=session_id,
            run_id=run_id,
            event_type=type,
        )
        return {
            "events": [
                _event_response(event.run_id, index, event)
                for index, event in enumerate(events)
            ]
        }

    @router.delete("/api/traces")
    def prune_traces(_: None = Depends(require_auth)) -> dict[str, object]:
        removed = trace_store.prune()
        return {"removed": removed}

    return router


def _event_response(run_id: str, index: int, event: Any) -> dict[str, object]:
    return {
        "id": f"event_{index}",
        "type": event.type,
        "session_id": event.session_id,
        "run_id": run_id,
        "payload": event.payload,
        "created_at": event.created_at.isoformat(),
    }
