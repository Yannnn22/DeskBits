import asyncio
import secrets
from contextlib import suppress

from fastapi import APIRouter, WebSocket
from pydantic import ValidationError
from starlette.websockets import WebSocketDisconnect

from desktopmate_agent.agent.service import AgentService
from desktopmate_agent.api.schemas import ClientEvent
from desktopmate_agent.models.provider import ModelUnavailableError
from desktopmate_agent.reminders.service import ReminderService
from desktopmate_agent.runtime.events import RuntimeEventBus
from desktopmate_agent.runtime.runs import RunRegistry
from desktopmate_agent.tools.permissions import PermissionDecision, PermissionRequestBroker


def create_websocket_router(
    agent_service: AgentService,
    permission_broker: PermissionRequestBroker,
    run_registry: RunRegistry | None = None,
    *,
    auth_token: str | None,
    allowed_origins: list[str] | None = None,
    runtime_bus: RuntimeEventBus | None = None,
    reminder_service: ReminderService | None = None,
) -> APIRouter:
    router = APIRouter()
    allowed_origins = allowed_origins or []
    active_run_registry = run_registry or RunRegistry()

    @router.websocket("/ws/chat")
    async def chat(websocket: WebSocket) -> None:
        if not _is_authorized(websocket, auth_token, allowed_origins):
            await websocket.close(code=1008)
            return
        await websocket.accept()
        send_lock = asyncio.Lock()
        active_stream_task: asyncio.Task[None] | None = None
        while True:
            try:
                raw_event = await websocket.receive_json()
            except WebSocketDisconnect:
                if active_stream_task is not None:
                    active_stream_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await active_stream_task
                break
            try:
                client_event = ClientEvent.model_validate(raw_event)
            except ValidationError:
                async with send_lock:
                    await websocket.send_json(
                        {
                            "type": "error",
                            "payload": {
                                "code": "invalid_client_event",
                                "message": "客户端事件格式不合法",
                            },
                        }
                    )
                continue

            if client_event.type == "permission_decision":
                request_id = client_event.request_id or ""
                try:
                    permission_broker.resolve(
                        request_id,
                        PermissionDecision(
                            decision=client_event.decision or "denied",
                            reason=client_event.reason,
                        ),
                    )
                except KeyError:
                    async with send_lock:
                        await websocket.send_json(
                            {
                                "type": "error",
                                "payload": {
                                    "code": "permission_request_not_found",
                                    "message": "权限请求不存在或已过期",
                                },
                            }
                        )
                continue

            if client_event.type == "user_message":
                if not client_event.text:
                    async with send_lock:
                        await websocket.send_json(
                            {
                                "type": "error",
                                "payload": {"code": "empty_message", "message": "消息不能为空"},
                            }
                        )
                    continue
                if active_stream_task is not None and not active_stream_task.done():
                    async with send_lock:
                        await websocket.send_json(
                            {
                                "type": "error",
                                "payload": {
                                    "code": "agent_run_in_progress",
                                    "message": "当前任务仍在执行",
                                },
                            }
                        )
                    continue
                active_stream_task = asyncio.create_task(
                    _stream_agent_events(
                        websocket=websocket,
                        send_lock=send_lock,
                        agent_service=agent_service,
                        run_registry=active_run_registry,
                        text=client_event.text,
                        session_id=client_event.session_id,
                        mode=client_event.mode or "agent",
                        attachments=client_event.attachments,
                        artifact_id=client_event.artifact_id,
                        workspace_context=client_event.workspace_context.model_dump()
                        if client_event.workspace_context is not None
                        else None,
                        project_id=client_event.project_id,
                        surface=client_event.surface,
                    )
                )

    @router.websocket("/ws/runtime")
    async def runtime(websocket: WebSocket) -> None:
        """桌宠实时事件订阅：连接后先推 runtime_status 快照，随后实时推送。

        鉴权与 /ws/chat 一致（query token / header token + origin 白名单）。
        """
        if not _is_authorized(websocket, auth_token, allowed_origins):
            await websocket.close(code=1008)
            return
        if runtime_bus is None:
            await websocket.close(code=1011)
            return
        await websocket.accept()
        send_lock = asyncio.Lock()
        sub_id: int | None = None

        async def push(event: dict[str, object]) -> None:
            async with send_lock:
                await websocket.send_json(event)

        try:
            # 冷启动：先推当前快照，桌宠立即有状态可展示
            await push(
                {
                    "type": "runtime_status",
                    "payload": runtime_bus.snapshot(
                        active_runs=len(active_run_registry.active_run_ids()),
                        pending_permissions=permission_broker.pending_count(),
                        due_reminders=reminder_service.due_reminders_count()
                        if reminder_service is not None
                        else 0,
                    ),
                }
            )
            sub_id = runtime_bus.subscribe(push)
            while True:
                await websocket.receive_text()  # 保活；忽略客户端消息
        except WebSocketDisconnect:
            pass
        finally:
            if sub_id is not None:
                runtime_bus.unsubscribe(sub_id)

    return router


def _is_authorized(
    websocket: WebSocket,
    auth_token: str | None,
    allowed_origins: list[str],
) -> bool:
    origin = websocket.headers.get("origin")
    if origin is not None and origin not in allowed_origins:
        return False
    expected = auth_token or ""
    query_token = websocket.query_params.get("token", "")
    header_token = websocket.headers.get("X-DesktopMate-Token", "") or _bearer_token(
        websocket.headers.get("Authorization", "")
    )
    provided = query_token or header_token or ""
    return bool(expected) and bool(provided) and secrets.compare_digest(provided, expected)


def _bearer_token(header: str) -> str:
    scheme, _, token = header.partition(" ")
    if scheme.lower() == "bearer" and token:
        return token
    return ""


async def _stream_agent_events(
    *,
    websocket: WebSocket,
    send_lock: asyncio.Lock,
    agent_service: AgentService,
    run_registry: RunRegistry,
    text: str,
    session_id: str | None,
    mode: str,
    attachments: list[str],
    artifact_id: str | None,
    workspace_context: dict[str, str] | None,
    project_id: str | None,
    surface: str,
) -> None:
    run_id: str | None = None
    active_session_id = session_id
    try:
        async for event in agent_service.stream_chat(
            text=text,
            session_id=session_id,
            mode=mode,
            attachments=attachments,
            artifact_id=artifact_id,
            workspace_context=workspace_context,
            project_id=project_id,
            surface=surface,
        ):
            if run_id is None and event.run_id is not None:
                run_id = event.run_id
                run_registry.register(run_id, asyncio.current_task())
            if event.session_id:
                active_session_id = event.session_id
            async with send_lock:
                await websocket.send_json(event.model_dump(mode="json"))
    except asyncio.CancelledError:
        if run_id is not None:
            # 任务已被取消：shield 保护发送，避免被再次取消。
            with suppress(asyncio.CancelledError):
                await asyncio.shield(
                    websocket.send_json(
                        {
                            "type": "error",
                            "session_id": active_session_id,
                            "run_id": run_id,
                            "payload": {"code": "run_cancelled", "message": "任务已取消"},
                        }
                    )
                )
    except ModelUnavailableError as exc:
        async with send_lock:
            await websocket.send_json(
                {
                    "type": "error",
                    "session_id": active_session_id,
                    "run_id": run_id,
                    "payload": {
                        "code": "model_unavailable",
                        "message": str(exc),
                        "model": exc.model,
                    },
                }
            )
    except Exception as exc:
        async with send_lock:
            await websocket.send_json(
                {
                    "type": "error",
                    "session_id": active_session_id,
                    "run_id": run_id,
                    "payload": {"code": "agent_run_failed", "message": str(exc)},
                }
            )
    finally:
        if run_id is not None:
            run_registry.unregister(run_id)
