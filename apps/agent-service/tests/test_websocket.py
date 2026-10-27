from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from desktopmate_agent.agent.intent import ModelIntentRouter
from desktopmate_agent.agent.service import AgentService
from desktopmate_agent.config import Settings
from desktopmate_agent.main import create_app
from desktopmate_agent.api.websocket import create_websocket_router
from desktopmate_agent.models.provider import FakeModelProvider
from desktopmate_agent.tools.base import BaseTool, RiskLevel, ToolResult
from desktopmate_agent.tools.permissions import PermissionRequestBroker
from desktopmate_agent.tools.registry import ToolRegistry
from desktopmate_agent.trace.store import JsonlTraceStore


WS_AUTH = "?token=test-token"


class ProtectedArgs(BaseModel):
    text: str


class ProtectedTool(BaseTool[ProtectedArgs]):
    name = "protected_tool"
    description = "Requires permission for websocket tests"
    args_schema = ProtectedArgs
    risk_level = RiskLevel.WRITE
    requires_permission = True

    async def run(self, args: ProtectedArgs) -> ToolResult:
        return ToolResult(status="success", summary=f"已执行：{args.text}", data={"text": args.text})


def test_websocket_chat_streams_events(tmp_path, no_api_settings):
    client = TestClient(create_app(trace_dir=tmp_path, settings=no_api_settings))

    with client.websocket_connect(f"/ws/chat{WS_AUTH}") as websocket:
        websocket.send_json({"type": "user_message", "text": "hello"})

        first = websocket.receive_json()
        second = websocket.receive_json()
        third = websocket.receive_json()

    assert first["type"] == "agent_started"
    assert second["type"] == "agent_message"
    assert second["payload"]["text"] == "收到。"
    assert third["type"] == "done"


def test_websocket_tool_chat_streams_tool_events(tmp_path, tmp_allowed_root):
    note = tmp_allowed_root / "note.md"
    note.write_text("# DesktopMate\n", encoding="utf-8")

    settings = Settings(
        trace_dir=tmp_path / "traces",
        allowed_roots=str(tmp_allowed_root),
        auth_token="test-token",
        openai_api_key=None,
        deepseek_api_key=None,
    )
    client = TestClient(create_app(trace_dir=tmp_path, settings=settings))

    with client.websocket_connect(f"/ws/chat{WS_AUTH}") as websocket:
        websocket.send_json({"type": "user_message", "text": "/search note", "mode": "agent"})

        events = [websocket.receive_json() for _ in range(5)]

    assert [event["type"] for event in events] == [
        "agent_started",
        "tool_call_started",
        "tool_call_finished",
        "agent_message",
        "done",
    ]
    assert events[1]["payload"]["tool_name"] == "search_files"
    assert events[1]["payload"]["risk_level"] == "read"
    assert events[1]["payload"]["requires_permission"] is False
    assert events[2]["payload"]["result"]["summary"] == "找到 1 个文件"


def test_websocket_reminder_request_creates_task_candidate_without_persisting(tmp_path, no_api_settings):
    client = TestClient(create_app(trace_dir=tmp_path, settings=no_api_settings))

    with client.websocket_connect(f"/ws/chat{WS_AUTH}") as websocket:
        websocket.send_json({"type": "user_message", "text": "提醒我明天下午一点吃药", "mode": "agent"})
        events = [websocket.receive_json() for _ in range(5)]

    assert [event["type"] for event in events] == [
        "agent_started",
        "tool_call_started",
        "tool_call_finished",
        "agent_message",
        "done",
    ]
    assert events[1]["payload"]["tool_name"] == "task_draft"
    candidate = events[2]["payload"]["result"]["data"]
    assert candidate["task"]["title"] == "吃药"
    assert candidate["reminder"]["due_at"]
    assert client.get("/api/tasks", headers={"X-DesktopMate-Token": "test-token"}).json() == {"tasks": []}
    assert client.get("/api/reminders?status=all", headers={"X-DesktopMate-Token": "test-token"}).json() == {"reminders": []}


def test_websocket_can_receive_permission_decision_during_active_chat(tmp_path):
    broker = PermissionRequestBroker(timeout_seconds=1)
    registry = ToolRegistry()
    registry.register(ProtectedTool())
    intent_router = ModelIntentRouter(
        model_provider=FakeModelProvider(
            response_text=(
                '{"type":"tool_call","tool_name":"protected_tool",'
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
    app = FastAPI()
    app.include_router(create_websocket_router(service, broker, auth_token="test-token"))
    client = TestClient(app)

    with client.websocket_connect(f"/ws/chat{WS_AUTH}") as websocket:
        websocket.send_json({"type": "user_message", "text": "run protected tool", "mode": "agent"})

        first = websocket.receive_json()
        permission_event = websocket.receive_json()
        websocket.send_json(
            {
                "type": "permission_decision",
                "request_id": permission_event["payload"]["request_id"],
                "decision": "approved",
            }
        )
        rest = [websocket.receive_json() for _ in range(4)]

    events = [first, permission_event, *rest]
    assert [event["type"] for event in events] == [
        "agent_started",
        "permission_request",
        "tool_call_started",
        "tool_call_finished",
        "agent_message",
        "done",
    ]
    assert events[2]["payload"]["risk_level"] == "write"
    assert events[4]["payload"]["text"] == "已执行：demo"


def test_websocket_unknown_permission_decision_returns_error(tmp_path, no_api_settings):
    client = TestClient(create_app(trace_dir=tmp_path, settings=no_api_settings))

    with client.websocket_connect(f"/ws/chat{WS_AUTH}") as websocket:
        websocket.send_json(
            {"type": "permission_decision", "request_id": "missing", "decision": "approved"}
        )

        event = websocket.receive_json()

    assert event["type"] == "error"
    assert event["payload"]["code"] == "permission_request_not_found"


def test_websocket_can_approve_draft_write_tool(tmp_path, tmp_allowed_root):
    target = tmp_allowed_root / "note.md"
    draft_dir = tmp_path / "drafts"
    settings = Settings(
        trace_dir=tmp_path / "traces",
        draft_dir=draft_dir,
        allowed_roots=str(tmp_allowed_root),
        auth_token="test-token",
        openai_api_key=None,
        deepseek_api_key=None,
    )
    client = TestClient(create_app(trace_dir=tmp_path, settings=settings))

    with client.websocket_connect(f"/ws/chat{WS_AUTH}") as websocket:
        websocket.send_json(
            {
                "type": "user_message",
                "text": f"/draft {target}\n# Draft note\n",
                "mode": "agent",
            }
        )

        first = websocket.receive_json()
        permission_event = websocket.receive_json()
        websocket.send_json(
            {
                "type": "permission_decision",
                "request_id": permission_event["payload"]["request_id"],
                "decision": "approved",
            }
        )
        rest = [websocket.receive_json() for _ in range(4)]

    events = [first, permission_event, *rest]
    assert [event["type"] for event in events] == [
        "agent_started",
        "permission_request",
        "tool_call_started",
        "tool_call_finished",
        "agent_message",
        "done",
    ]
    draft_path = Path(events[3]["payload"]["result"]["data"]["draft_path"])
    assert target.exists() is False
    assert draft_path.exists()
    assert "target_path:" in draft_path.read_text(encoding="utf-8")
