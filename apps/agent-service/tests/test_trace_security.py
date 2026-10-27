import os

import pytest

from desktopmate_agent.agent.service import AgentService
from desktopmate_agent.api.schemas import ServerEvent
from desktopmate_agent.models.provider import FakeModelProvider
from desktopmate_agent.tools.file_tools import ReadFileTool
from desktopmate_agent.tools.registry import ToolRegistry
from desktopmate_agent.trace.events import TraceEvent
from desktopmate_agent.trace.store import JsonlTraceStore


@pytest.mark.asyncio
async def test_trace_redacts_file_content_but_client_event_keeps_it(tmp_path, tmp_allowed_root):
    note = tmp_allowed_root / "secret.md"
    note.write_text("# 机密内容\n", encoding="utf-8")
    registry = ToolRegistry()
    registry.register(ReadFileTool(allowed_roots=[tmp_allowed_root]))
    trace_store = JsonlTraceStore(tmp_path / "traces")
    service = AgentService(
        model_provider=FakeModelProvider(),
        tool_registry=registry,
        trace_store=trace_store,
    )

    events = [
        event async for event in service.stream_chat(text=f"/read {note}", session_id=None, mode="agent")
    ]
    run_id = events[0].run_id
    finished = next(event for event in events if event.type == "tool_call_finished")
    assert finished.payload["result"]["data"]["content"] == "# 机密内容\n"

    traced = trace_store.read_run(run_id)
    traced_finished = next(event for event in traced if event.type == "tool_call_finished")
    assert traced_finished.payload["result"]["data"]["content"] == "<redacted>"
    assert "机密内容" not in traced_finished.payload["result"]["data"]["content"]
    traced_message = next(event for event in traced if event.type == "agent_message")
    assert traced_message.payload["text"] == "读取文件成功"
    assert traced_message.payload["tool_results"][0]["data"]["content"] == "<redacted>"


def test_redaction_removes_draft_content_from_permission_payload(tmp_path):
    service = AgentService(
        model_provider=FakeModelProvider(),
        tool_registry=ToolRegistry(),
        trace_store=JsonlTraceStore(tmp_path / "traces"),
    )
    event = ServerEvent(
        type="permission_request",
        session_id="session_1",
        run_id="run_1",
        payload={
            "tool_name": "draft_write",
            "args": {"path": "/tmp/note.md", "content": "secret draft body"},
        },
    )

    redacted = service._redact_for_trace(event)

    assert redacted.payload["args"]["content"] == "<redacted>"
    assert redacted.payload["args"]["path"] == "/tmp/note.md"


def test_trace_store_prunes_old_runs(tmp_path):
    store = JsonlTraceStore(tmp_path / "traces", retention_days=None)
    event = TraceEvent(type="agent_started", session_id="session_1", run_id="run_old")
    store.append(event)
    path = tmp_path / "traces" / "run_old.jsonl"
    assert path.exists()

    old_mtime = os.path.getmtime(path) - 2 * 86_400
    os.utime(path, (old_mtime, old_mtime))

    JsonlTraceStore(tmp_path / "traces", retention_days=1)

    assert not path.exists()
