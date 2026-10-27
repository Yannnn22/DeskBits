from desktopmate_agent.api.schemas import ChatRequest
from desktopmate_agent.trace.events import TraceEvent
from desktopmate_agent.trace.store import JsonlTraceStore


def test_chat_request_defaults_session_id():
    request = ChatRequest(text="hello")

    assert request.text == "hello"
    assert request.session_id is None


def test_trace_event_contains_public_fields_only():
    event = TraceEvent(
        type="agent_message",
        session_id="s1",
        run_id="r1",
        payload={"text": "公开摘要"},
    )

    data = event.model_dump()
    assert data["type"] == "agent_message"
    assert data["payload"] == {"text": "公开摘要"}
    assert "chain_of_thought" not in data


def test_jsonl_trace_store_writes_and_reads_run(tmp_path):
    store = JsonlTraceStore(tmp_path)
    event = TraceEvent(type="agent_message", session_id="s1", run_id="r1", payload={"text": "hi"})

    store.append(event)

    events = store.read_run("r1")
    assert len(events) == 1
    assert events[0].type == "agent_message"
    assert events[0].payload == {"text": "hi"}
