"""会话与消息历史 API。"""

from fastapi import APIRouter, Depends, HTTPException, Query

from desktopmate_agent.api.routes import require_auth
from desktopmate_agent.api.schemas import (
    MessageCreate,
    MessageResponse,
    SessionCreate,
    SessionResponse,
    SessionUpdate,
)
from desktopmate_agent.sessions.store import SessionMessage, SessionRecord, SessionStore


def create_session_router(session_store: SessionStore) -> APIRouter:
    router = APIRouter()

    @router.get("/api/sessions")
    def list_sessions(
        scope: str = Query(default="recent", pattern="^(recent|all|archived)$"),
        _: None = Depends(require_auth),
    ) -> dict[str, object]:
        sessions = session_store.list_sessions(scope=scope)  # type: ignore[arg-type]
        return {"sessions": [_session_response(session) for session in sessions]}

    @router.post("/api/sessions")
    def create_session(
        payload: SessionCreate,
        _: None = Depends(require_auth),
    ) -> SessionResponse:
        session = session_store.create_session(title=payload.title, mode=payload.mode)
        return _session_response(session)

    @router.get("/api/sessions/{session_id}")
    def get_session(
        session_id: str,
        _: None = Depends(require_auth),
    ) -> SessionResponse:
        session = _require_session(session_store, session_id)
        return _session_response(session)

    @router.get("/api/sessions/{session_id}/messages")
    def list_messages(
        session_id: str,
        _: None = Depends(require_auth),
    ) -> dict[str, object]:
        _require_session(session_store, session_id)
        messages = session_store.list_messages(session_id)
        return {"messages": [_message_response(message) for message in messages]}

    @router.post("/api/sessions/{session_id}/messages")
    def add_message(
        session_id: str,
        payload: MessageCreate,
        _: None = Depends(require_auth),
    ) -> MessageResponse:
        _require_session(session_store, session_id)
        message = session_store.add_message(
            session_id,
            role=payload.role,
            text=payload.text,
            metadata=payload.metadata,
        )
        return _message_response(message)

    @router.patch("/api/sessions/{session_id}")
    def update_session(
        session_id: str,
        payload: SessionUpdate,
        _: None = Depends(require_auth),
    ) -> SessionResponse:
        _require_session(session_store, session_id)
        updated = session_store.update_session(
            session_id,
            title=payload.title,
            archived=payload.archived,
        )
        assert updated is not None
        return _session_response(updated)

    @router.delete("/api/sessions/{session_id}")
    def delete_session(
        session_id: str,
        _: None = Depends(require_auth),
    ) -> dict[str, bool]:
        _require_session(session_store, session_id)
        session_store.delete_session(session_id)
        return {"deleted": True}

    return router


def _require_session(session_store: SessionStore, session_id: str) -> SessionRecord:
    session = session_store.get_session(session_id)
    if session is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "session_not_found", "message": f"会话 {session_id} 不存在"},
        )
    return session


def _session_response(session: SessionRecord) -> SessionResponse:
    return SessionResponse(
        id=session.id,
        title=session.title,
        mode=session.mode,
        archived=session.archived,
        created_at=session.created_at,
        updated_at=session.updated_at,
    )


def _message_response(message: SessionMessage) -> MessageResponse:
    return MessageResponse(
        id=message.id,
        session_id=message.session_id,
        role=message.role,
        text=message.text,
        created_at=message.created_at,
        metadata=message.metadata,
    )
