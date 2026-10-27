from typing import Any

from pydantic import BaseModel, Field


class AgentRunState(BaseModel):
    session_id: str
    run_id: str
    user_text: str
    mode: str = "agent"
    artifact_id: str | None = None
    workspace_context: dict[str, str] | None = None
    # 消息可携带附加标记（如 tool_summary），故值为任意类型
    messages: list[dict[str, Any]] = Field(default_factory=list)
    memory_context: list[dict[str, str]] = Field(default_factory=list)
    history: list[dict[str, str]] = Field(default_factory=list)
    tool_calls: list[dict] = Field(default_factory=list)
    tool_results: list[dict] = Field(default_factory=list)
    pending_tool_call: dict | None = None
    tool_step_count: int = 0
    knowledge_context: list[dict[str, str]] = Field(default_factory=list)
    citations: list[dict[str, str]] = Field(default_factory=list)
    image_contents: list[dict] = Field(default_factory=list)
    # 文档附件文本（方案 B：注入 prompt 供直接对话）
    document_contexts: list[dict[str, str]] = Field(default_factory=list)
