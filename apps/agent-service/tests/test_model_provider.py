import asyncio

import pytest

from desktopmate_agent.config import Settings
from desktopmate_agent.agent.intent import HeuristicIntentRouter, ModelIntentRouter, ReActIntentRouter
from desktopmate_agent.models.provider import (
    FakeModelProvider,
    ModelUnavailableError,
    OpenAICompatibleProvider,
    create_intent_router,
    create_model_provider,
)


class MissingModelError(RuntimeError):
    body = {"error": {"code": "model_not_found"}}


class MissingModelCompletions:
    async def create(self, **_: object) -> object:
        raise MissingModelError("model_not_found")


class MissingModelClient:
    class Chat:
        completions = MissingModelCompletions()

    chat = Chat()


class Delta:
    def __init__(self, content: str | None = None, reasoning_content: str | None = None) -> None:
        self.content = content
        self.reasoning_content = reasoning_content


class Choice:
    def __init__(self, delta: Delta) -> None:
        self.delta = delta


class StreamChunk:
    def __init__(self, delta: Delta) -> None:
        self.choices = [Choice(delta)]


class StreamingCompletions:
    def __init__(self, chunks: list[StreamChunk]) -> None:
        self.chunks = chunks

    async def create(self, **_: object) -> object:
        async def stream():
            for chunk in self.chunks:
                yield chunk

        return stream()


class StreamingClient:
    def __init__(self, chunks: list[StreamChunk]) -> None:
        class Chat:
            completions = StreamingCompletions(chunks)

        self.chat = Chat()


def test_create_model_provider_uses_fake_without_api_key():
    provider = create_model_provider(Settings(openai_api_key=None, deepseek_api_key=None))

    assert isinstance(provider, FakeModelProvider)


def test_create_model_provider_prefers_deepseek_when_key_is_configured():
    provider = create_model_provider(
        Settings(
            deepseek_api_key="test-key",
            deepseek_model="deepseek-test",
            deepseek_base_url="https://example.invalid/v1",
        )
    )

    assert isinstance(provider, OpenAICompatibleProvider)
    assert provider.model == "deepseek-test"
    assert provider.base_url == "https://example.invalid/v1"


def test_create_intent_router_uses_react_by_default():
    router = create_intent_router(
        Settings(openai_api_key=None, deepseek_api_key=None),
        model_provider=FakeModelProvider(),
    )

    assert isinstance(router, ReActIntentRouter)


def test_create_intent_router_can_use_heuristic_router():
    router = create_intent_router(
        Settings(intent_router="heuristic", openai_api_key=None, deepseek_api_key=None),
        model_provider=FakeModelProvider(),
    )

    assert isinstance(router, HeuristicIntentRouter)


def test_create_intent_router_can_use_model_router():
    router = create_intent_router(
        Settings(intent_router="model", openai_api_key=None, deepseek_api_key=None),
        model_provider=FakeModelProvider(),
    )

    assert isinstance(router, ModelIntentRouter)


def test_openai_provider_detects_vision_model():
    provider = create_model_provider(
        Settings(
            openai_api_key="test-key",
            openai_model="gpt-4.1-mini",
            openai_base_url=None,
            deepseek_api_key=None,
        )
    )
    assert provider.supports_vision() is True


def test_deepseek_provider_does_not_support_vision():
    provider = create_model_provider(
        Settings(
            deepseek_api_key="test-key",
            deepseek_model="deepseek-v4-flash",
            deepseek_base_url="https://api.deepseek.com",
            openai_api_key=None,
        )
    )
    assert provider.supports_vision() is False


@pytest.mark.asyncio
async def test_openai_provider_translates_distributor_model_not_found():
    provider = OpenAICompatibleProvider(
        api_key="test-key",
        model="unavailable-model",
        base_url="https://example.invalid/v1",
    )
    provider.client = MissingModelClient()  # type: ignore[assignment]

    with pytest.raises(ModelUnavailableError, match="unavailable-model"):
        await provider.complete("hello")


def test_stream_complete_emits_reasoning_and_content():
    provider = OpenAICompatibleProvider(
        api_key="test-key",
        model="test-model",
        base_url="https://example.invalid/v1",
    )
    provider.client = StreamingClient(
        [
            StreamChunk(Delta(reasoning_content="先想一下")),
            StreamChunk(Delta(content="你好")),
            StreamChunk(Delta(content="，世界")),
        ]
    )  # type: ignore[assignment]
    reasoning_parts: list[str] = []

    async def run() -> list[str]:
        return [
            part
            async for part in provider.stream_complete(
                "hello",
                on_reasoning=reasoning_parts.append,
            )
        ]

    content = asyncio.run(run())

    assert "".join(content) == "你好，世界"
    assert reasoning_parts == ["先想一下"]


def test_stream_complete_without_reasoning_callback_keeps_content_behavior():
    provider = OpenAICompatibleProvider(
        api_key="test-key",
        model="test-model",
        base_url="https://example.invalid/v1",
    )
    provider.client = StreamingClient(
        [
            StreamChunk(Delta(reasoning_content="不会被回调")),
            StreamChunk(Delta(content="照常")),
            StreamChunk(Delta(content="返回")),
        ]
    )  # type: ignore[assignment]

    async def run() -> list[str]:
        return [part async for part in provider.stream_complete("hello")]

    content = asyncio.run(run())

    assert "".join(content) == "照常返回"


def test_stream_content_emits_reasoning_and_content():
    provider = OpenAICompatibleProvider(
        api_key="test-key",
        model="test-model",
        base_url="https://example.invalid/v1",
    )
    provider.client = StreamingClient(
        [
            StreamChunk(Delta(reasoning_content="看图思考")),
            StreamChunk(Delta(content="图像")),
            StreamChunk(Delta(content="回答")),
        ]
    )  # type: ignore[assignment]
    reasoning_parts: list[str] = []

    async def run() -> list[str]:
        return [
            part
            async for part in provider.stream_content(
                [{"type": "text", "text": "describe"}],
                on_reasoning=reasoning_parts.append,
            )
        ]

    content = asyncio.run(run())

    assert "".join(content) == "图像回答"
    assert reasoning_parts == ["看图思考"]
