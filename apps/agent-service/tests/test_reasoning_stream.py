import asyncio
from collections.abc import AsyncIterator, Callable

from desktopmate_agent.agent.service import AgentService
from desktopmate_agent.models.provider import FakeModelProvider
from desktopmate_agent.tools.registry import ToolRegistry
from desktopmate_agent.trace.store import JsonlTraceStore


class ReasoningFakeProvider(FakeModelProvider):
    async def stream_complete(
        self,
        prompt: str,
        on_reasoning: Callable[[str], None] | None = None,
    ) -> AsyncIterator[str]:
        self.prompts.append(prompt)
        if on_reasoning is not None:
            on_reasoning("先分析一下")
        yield "最终回答"


def test_agent_service_streams_agent_thinking_before_message(tmp_path):
    service = AgentService(
        model_provider=ReasoningFakeProvider(),
        tool_registry=ToolRegistry(),
        trace_store=JsonlTraceStore(tmp_path / "traces"),
    )

    async def run():
        return [
            event
            async for event in service.stream_chat(text="hello", session_id="s1", mode="agent")
        ]

    events = asyncio.run(run())
    types = [event.type for event in events]

    assert "agent_thinking" in types
    assert types.index("agent_thinking") < types.index("agent_message")
    assert next(event for event in events if event.type == "agent_thinking").payload["text"] == "先分析一下"
