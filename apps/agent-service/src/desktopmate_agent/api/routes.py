import secrets

from fastapi import APIRouter, Depends, HTTPException, Request

from desktopmate_agent import __version__
from desktopmate_agent.agent.service import AgentService
from desktopmate_agent.api.schemas import ChatRequest, ChatResponse
from desktopmate_agent.models.provider import resolve_model_config
from desktopmate_agent.tools.permissions import PermissionDecision


def create_router(
    agent_service: AgentService,
    *,
    features: dict[str, bool] | None = None,
) -> APIRouter:
    router = APIRouter()
    active_features = features or {}

    @router.get("/")
    def index(request: Request) -> dict[str, object]:
        return {
            "service": "desktopmate-agent-service",
            "version": __version__,
            "auth_enabled": bool(request.app.state.auth_token),
            "api": {
                "health": "/api/health",
                "runtime": "/api/runtime",
                "tools": "/api/tools",
                "chat": "/api/chat/invoke",
                "sessions": "/api/sessions",
                "knowledge": "/api/knowledge/documents",
                "index": "/api/knowledge/index/status",
                "reminders": "/api/reminders",
                "speech": "/api/speech/status",
                "storage": "/api/storage/usage",
                "skills": "/api/skills",
                "settings": "/api/settings/profile",
                "trace": "/api/events",
            },
        }

    @router.get("/health")
    def health(request: Request) -> dict[str, object]:
        return {
            "status": "ready",
            "service": "desktopmate-agent-service",
            "version": __version__,
            "auth_enabled": bool(request.app.state.auth_token),
            "readiness": "/api/health",
        }

    @router.get("/api/runtime")
    def runtime(
        request: Request,
        _: None = Depends(require_auth),
    ) -> dict[str, object]:
        settings = request.app.state.settings
        settings_store = request.app.state.settings_store
        resolved = resolve_model_config(settings, settings_store)
        whisper_model_dir = settings.whisper_model_dir or (settings.app_data_dir / "models" / "whisper")
        model_cached = any(whisper_model_dir.glob("*")) if whisper_model_dir.exists() else False
        return {
            "service": "desktopmate-agent-service",
            "version": __version__,
            "auth_enabled": bool(request.app.state.auth_token),
            "features": {
                **active_features,
                "speech": True,
                "attachments": True,
                "vision": resolved.vision_enabled,
            },
            "model": {
                "provider": resolved.provider,
                "name": resolved.model,
                "api_key_source": resolved.api_key_source,
                "vision_enabled": resolved.vision_enabled,
            },
            "speech": {
                "enabled": True,
                "model": settings.whisper_model,
                "device": settings.whisper_device,
                "model_cached": model_cached,
            },
        }

    @router.get("/api/tools")
    def tools(
        request: Request,
        _: None = Depends(require_auth),
    ) -> dict[str, object]:
        registry = request.app.state.agent_service.tool_registry
        return {
            "tools": [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "risk_level": str(tool.risk_level),
                    "requires_permission": tool.requires_permission,
                    "enabled": registry.is_enabled(tool.name),
                    "args_schema": tool.args_schema.model_json_schema(),
                }
                for tool in registry.list()
            ]
        }

    @router.post("/api/chat/invoke")
    async def invoke(
        chat_request: ChatRequest,
        request: Request,
        _: None = Depends(require_auth),
    ) -> ChatResponse:
        final_text = ""
        session_id = chat_request.session_id or ""
        run_id = ""
        requires_websocket = False
        async for event in agent_service.stream_chat(
            text=chat_request.text,
            session_id=chat_request.session_id,
            mode=chat_request.mode or "agent",
            attachments=chat_request.attachments,
            artifact_id=chat_request.artifact_id,
            workspace_context=chat_request.workspace_context.model_dump()
            if chat_request.workspace_context is not None
            else None,
            project_id=chat_request.project_id,
            surface=chat_request.surface,
        ):
            if event.session_id:
                session_id = event.session_id
            if event.run_id:
                run_id = event.run_id
            if event.type == "permission_request":
                # HTTP 是一次性请求，无法承接异步的人工确认回传。明确拒绝本次操作，
                # 让服务端持久化取消结果；桌面端应改走 WebSocket 以展示确认卡片。
                requires_websocket = True
                request_id = str(event.payload.get("request_id", ""))
                if agent_service.permission_broker is not None and request_id:
                    agent_service.permission_broker.resolve(
                        request_id,
                        PermissionDecision(
                            decision="denied",
                            reason=(
                                "该操作需要人工确认，但 HTTP 聊天接口无法弹出确认框。"
                                "请在桌面聊天窗口中重试，或切换到 Agent 模式通过确认卡片批准。"
                            ),
                        ),
                    )
            if event.type == "agent_message":
                final_text = str(event.payload.get("text", ""))
        return ChatResponse(
            session_id=session_id,
            run_id=run_id,
            text=final_text,
            requires_websocket=requires_websocket,
        )

    @router.post("/api/runs/{run_id}/cancel")
    def cancel_run(
        run_id: str,
        request: Request,
        _: None = Depends(require_auth),
    ) -> dict[str, bool]:
        registry = request.app.state.run_registry
        if not registry.cancel(run_id):
            raise HTTPException(
                status_code=404,
                detail={"code": "run_not_found", "message": f"运行 {run_id} 不存在或已结束"},
            )
        return {"cancelled": True}

    return router


def require_auth(request: Request) -> None:
    expected = request.app.state.auth_token
    provided = request.headers.get("X-DesktopMate-Token") or _bearer_token(
        request.headers.get("Authorization", "")
    )
    if not expected or not provided or not secrets.compare_digest(provided, expected):
        raise _unauthorized()


def _unauthorized():
    from fastapi import HTTPException

    return HTTPException(
        status_code=401,
        detail={"code": "unauthorized", "message": "Unauthorized"},
    )


def _bearer_token(header: str) -> str | None:
    scheme, _, token = header.partition(" ")
    if scheme.lower() == "bearer" and token:
        return token
    return None
