from typing import Any, Literal

from pydantic import BaseModel, Field


MAX_CHAT_TEXT_LENGTH = 200_000
MAX_ID_LENGTH = 128


class WorkspaceContext(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    kind: str = Field(min_length=1, max_length=64)
    content: str = Field(min_length=1, max_length=20_000)


class ChatRequest(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_CHAT_TEXT_LENGTH)
    session_id: str | None = Field(default=None, max_length=MAX_ID_LENGTH)
    mode: Literal["chat", "agent", "plan"] | None = None
    attachments: list[str] = Field(default_factory=list, max_length=32)
    artifact_id: str | None = Field(default=None, max_length=MAX_ID_LENGTH)
    workspace_context: WorkspaceContext | None = None
    project_id: str | None = Field(default=None, max_length=MAX_ID_LENGTH)
    surface: Literal["main", "pet"] = "main"


class ChatResponse(BaseModel):
    session_id: str
    run_id: str
    text: str
    # D5: 本次请求遇到需人工确认的操作（HTTP 无法弹确认框，已被拒绝）。
    # 客户端可据此提示用户改用桌面聊天窗口/WebSocket 重试。
    requires_websocket: bool = False


class ClientEvent(BaseModel):
    type: Literal["user_message", "permission_decision"]
    session_id: str | None = Field(default=None, max_length=MAX_ID_LENGTH)
    run_id: str | None = Field(default=None, max_length=MAX_ID_LENGTH)
    request_id: str | None = Field(default=None, max_length=MAX_ID_LENGTH)
    text: str | None = Field(default=None, max_length=MAX_CHAT_TEXT_LENGTH)
    mode: Literal["chat", "agent", "plan"] | None = None
    attachments: list[str] = Field(default_factory=list, max_length=32)
    artifact_id: str | None = Field(default=None, max_length=MAX_ID_LENGTH)
    workspace_context: WorkspaceContext | None = None
    project_id: str | None = Field(default=None, max_length=MAX_ID_LENGTH)
    surface: Literal["main", "pet"] = "main"
    decision: Literal["approved", "denied"] | None = None
    reason: str | None = Field(default=None, max_length=4_000)


class ServerEvent(BaseModel):
    type: str
    session_id: str | None = None
    run_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class SessionCreate(BaseModel):
    title: str = Field(default="New Conversation", min_length=1, max_length=200)
    mode: Literal["chat", "agent", "plan"] = "agent"


class SessionUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    archived: bool | None = None


class SessionResponse(BaseModel):
    id: str
    title: str
    mode: str
    archived: bool
    created_at: str
    updated_at: str


class MessageCreate(BaseModel):
    role: Literal["user", "assistant", "system"] = "user"
    text: str = Field(min_length=1, max_length=MAX_CHAT_TEXT_LENGTH)
    metadata: dict[str, Any] = Field(default_factory=dict)


class MessageResponse(BaseModel):
    id: str
    session_id: str
    role: str
    text: str
    created_at: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class KnowledgeDocumentCreate(BaseModel):
    collection_id: str | None = Field(default=None, max_length=MAX_ID_LENGTH)
    importance: float = Field(default=0.5, ge=0.0, le=1.0)
