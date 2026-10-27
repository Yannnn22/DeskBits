from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel
from starlette.websockets import WebSocketDisconnect

from desktopmate_agent.agent.intent import ModelIntentRouter
from desktopmate_agent.agent.service import AgentService
from desktopmate_agent.config import Settings
from desktopmate_agent.main import create_app
from desktopmate_agent.models.provider import FakeModelProvider
from desktopmate_agent.tools.base import BaseTool, RiskLevel, ToolResult
from desktopmate_agent.tools.permissions import PermissionRequestBroker
from desktopmate_agent.tools.registry import ToolRegistry
from desktopmate_agent.trace.store import JsonlTraceStore


AUTH_TOKEN = "secret-token"
AUTH_HEADERS = {"X-DesktopMate-Token": AUTH_TOKEN}


def make_settings(tmp_path: Path, *, allowed_origins: str = "") -> Settings:
    return Settings(
        trace_dir=tmp_path / "traces",
        auth_token=AUTH_TOKEN,
        allowed_origins=allowed_origins,
        openai_api_key=None,
        deepseek_api_key=None,
    )


def test_health_stays_open_without_token(tmp_path):
    client = TestClient(create_app(settings=make_settings(tmp_path)))

    response = client.get("/health")

    assert response.status_code == 200


def test_http_invoke_requires_token(tmp_path):
    client = TestClient(create_app(settings=make_settings(tmp_path)))

    missing = client.post("/api/chat/invoke", json={"text": "hello"})
    wrong = client.post(
        "/api/chat/invoke",
        json={"text": "hello"},
        headers={"X-DesktopMate-Token": "wrong-token"},
    )
    with_header = client.post(
        "/api/chat/invoke",
        json={"text": "hello"},
        headers=AUTH_HEADERS,
    )
    with_bearer = client.post(
        "/api/chat/invoke",
        json={"text": "hello"},
        headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
    )

    assert missing.status_code == 401
    assert wrong.status_code == 401
    assert with_header.status_code == 200
    assert with_bearer.status_code == 200


def test_websocket_rejects_connection_without_token(tmp_path):
    client = TestClient(create_app(settings=make_settings(tmp_path)))

    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/chat"):
            pass


def test_websocket_accepts_token_via_query(tmp_path):
    client = TestClient(create_app(settings=make_settings(tmp_path)))

    with client.websocket_connect(f"/ws/chat?token={AUTH_TOKEN}") as websocket:
        websocket.send_json({"type": "user_message", "text": "hello"})
        events = [websocket.receive_json() for _ in range(3)]

    assert [event["type"] for event in events] == [
        "agent_started",
        "agent_message",
        "done",
    ]


def test_websocket_rejects_unlisted_origin(tmp_path):
    client = TestClient(create_app(settings=make_settings(tmp_path)))

    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(
            f"/ws/chat?token={AUTH_TOKEN}",
            headers={"origin": "https://evil.example"},
        ):
            pass


def test_websocket_accepts_listed_origin(tmp_path):
    client = TestClient(
        create_app(
            settings=make_settings(
                tmp_path,
                allowed_origins="http://localhost:5173",
            )
        )
    )

    with client.websocket_connect(
        f"/ws/chat?token={AUTH_TOKEN}",
        headers={"origin": "http://localhost:5173"},
    ) as websocket:
        websocket.send_json({"type": "user_message", "text": "hello"})
        events = [websocket.receive_json() for _ in range(3)]

    assert events[-1]["type"] == "done"


def test_websocket_invalid_client_event_returns_error(tmp_path):
    client = TestClient(create_app(settings=make_settings(tmp_path)))

    with client.websocket_connect(f"/ws/chat?token={AUTH_TOKEN}") as websocket:
        websocket.send_json({"type": "unknown_event"})
        event = websocket.receive_json()

    assert event["type"] == "error"
    assert event["payload"]["code"] == "invalid_client_event"


class WriteArgs(BaseModel):
    text: str


class WriteWithoutFlagTool(BaseTool[WriteArgs]):
    name = "write_without_flag"
    description = "Write-risk tool that forgets its permission flag"
    args_schema = WriteArgs
    risk_level = RiskLevel.WRITE
    requires_permission = False

    async def run(self, args: WriteArgs) -> ToolResult:
        return ToolResult(status="success", summary=f"已执行：{args.text}", data={"text": args.text})


@pytest.mark.asyncio
async def test_risk_level_forces_permission_even_without_flag(tmp_path):
    broker = PermissionRequestBroker(timeout_seconds=1)
    registry = ToolRegistry()
    registry.register(WriteWithoutFlagTool())
    intent_router = ModelIntentRouter(
        model_provider=FakeModelProvider(
            response_text=(
                '{"type":"tool_call","tool_name":"write_without_flag",'
                '"tool_args":{"text":"demo"},"confidence":0.9}'
            )
        )
    )
    service = AgentService(
        model_provider=FakeModelProvider(),
        tool_registry=registry,
        trace_store=JsonlTraceStore(tmp_path),
        intent_router=intent_router,
        permission_broker=broker,
    )

    stream = service.stream_chat(text="run write tool", session_id=None, mode="agent")
    first = await anext(stream)
    permission_event = await anext(stream)

    assert first.type == "agent_started"
    assert permission_event.type == "permission_request"
    assert permission_event.payload["tool_name"] == "write_without_flag"
    await stream.aclose()
