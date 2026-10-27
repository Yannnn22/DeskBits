from dataclasses import dataclass
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, AsyncIterator, NoReturn, Protocol

from openai import AsyncOpenAI

from desktopmate_agent.agent.intent import HeuristicIntentRouter, IntentRouter, ModelIntentRouter, ReActIntentRouter
from desktopmate_agent.config import Settings
from desktopmate_agent.settings.store import SettingsStore

if TYPE_CHECKING:
    from desktopmate_agent.tools.registry import ToolRegistry


class ModelProvider(Protocol):
    async def complete(self, prompt: str) -> str:
        raise NotImplementedError

    def stream_complete(
        self,
        prompt: str,
        on_reasoning: Callable[[str], None] | None = None,
    ) -> AsyncIterator[str]:
        raise NotImplementedError

    def stream_content(
        self,
        content: list[dict[str, Any]],
        on_reasoning: Callable[[str], None] | None = None,
    ) -> AsyncIterator[str]:
        raise NotImplementedError

    def supports_vision(self) -> bool:
        raise NotImplementedError


class ModelUnavailableError(RuntimeError):
    """The configured OpenAI-compatible endpoint cannot serve the selected model."""

    def __init__(self, model: str) -> None:
        self.model = model
        super().__init__(
            f'模型“{model}”在当前 API 渠道不可用。请在设置的模型页刷新可用模型后重新选择。'
        )


class FakeModelProvider:
    def __init__(self, response_text: str = "收到。") -> None:
        self.response_text = response_text
        self.prompts: list[str] = []

    async def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.response_text

    def supports_vision(self) -> bool:
        return False

    async def complete_content(self, content: list[dict[str, Any]]) -> str:
        self.prompts.append(str(content))
        return self.response_text


class OpenAICompatibleProvider:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str | None = None,
        supports_vision: bool = False,
    ) -> None:
        self.model = model
        self.base_url = base_url
        self._supports_vision = supports_vision
        self.client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=60.0,
            max_retries=1,
        )

    def supports_vision(self) -> bool:
        return self._supports_vision

    async def complete(self, prompt: str) -> str:
        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
            )
        except Exception as exc:
            _raise_provider_error(exc, self.model)
        message = response.choices[0].message.content
        return message or ""

    async def complete_content(self, content: list[dict[str, Any]]) -> str:
        """多模态补全：content 为 OpenAI content 块数组（text/image_url）。"""
        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": content}],
            )
        except Exception as exc:
            _raise_provider_error(exc, self.model)
        message = response.choices[0].message.content
        return message or ""

    async def stream_complete(
        self,
        prompt: str,
        on_reasoning: Callable[[str], None] | None = None,
    ) -> AsyncIterator[str]:
        try:
            stream = await self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                stream=True,
            )
            # 合并小碎片：累积到约 12 字符或遇到换行再下发，减少前端割裂感
            buffer = ""
            reasoning_buffer = ""
            async for chunk in stream:
                delta_obj = chunk.choices[0].delta
                reasoning = getattr(delta_obj, "reasoning_content", None)
                if reasoning and on_reasoning is not None:
                    reasoning_buffer += str(reasoning)
                    if len(reasoning_buffer) >= 80 or "\n" in reasoning_buffer:
                        on_reasoning(reasoning_buffer)
                        reasoning_buffer = ""
                delta = delta_obj.content
                if not delta:
                    continue
                buffer += delta
                if len(buffer) >= 12 or "\n" in buffer:
                    yield buffer
                    buffer = ""
            if reasoning_buffer and on_reasoning is not None:
                on_reasoning(reasoning_buffer)
            if buffer:
                yield buffer
        except Exception as exc:
            _raise_provider_error(exc, self.model)

    async def stream_content(
        self,
        content: list[dict[str, Any]],
        on_reasoning: Callable[[str], None] | None = None,
    ) -> AsyncIterator[str]:
        try:
            stream = await self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": content}],
                stream=True,
            )
            buffer = ""
            reasoning_buffer = ""
            async for chunk in stream:
                delta_obj = chunk.choices[0].delta
                reasoning = getattr(delta_obj, "reasoning_content", None)
                if reasoning and on_reasoning is not None:
                    reasoning_buffer += str(reasoning)
                    if len(reasoning_buffer) >= 80 or "\n" in reasoning_buffer:
                        on_reasoning(reasoning_buffer)
                        reasoning_buffer = ""
                delta = delta_obj.content
                if not delta:
                    continue
                buffer += delta
                if len(buffer) >= 12 or "\n" in buffer:
                    yield buffer
                    buffer = ""
            if reasoning_buffer and on_reasoning is not None:
                on_reasoning(reasoning_buffer)
            if buffer:
                yield buffer
        except Exception as exc:
            _raise_provider_error(exc, self.model)


def _raise_provider_error(exc: Exception, model: str) -> NoReturn:
    """Convert a common distributor-specific error into a stable app error."""
    body = getattr(exc, "body", None)
    error = body.get("error", body) if isinstance(body, dict) else {}
    code = str(error.get("code", "")) if isinstance(error, dict) else ""
    message = str(exc)
    if code in {"model_not_found", "model_not_available"} or "model_not_found" in message:
        raise ModelUnavailableError(model) from exc
    raise exc


_VISION_MODELS = {
    "gpt-4.1",
    "gpt-4o",
    "gpt-4o-mini",
    "gpt-4-vision",
    "gpt-5",  # gpt-5.x 系列（含 5.4/5.5/5.6）支持图片输入
    "claude-3",
    "claude-3.5",
    "claude-3.7",
    "claude-4",
}


def _model_supports_vision(model: str, *, base_url: str | None) -> bool:
    lowered = model.lower()
    # 已知文本模型提供商（DeepSeek 等）不支持视觉
    if base_url and "deepseek" in base_url.lower():
        return False
    return any(lowered.startswith(prefix) for prefix in _VISION_MODELS)


@dataclass
class ModelRuntimeInfo:
    """当前生效的模型配置（供 /api/runtime 与日志展示）。"""

    provider: str
    model: str
    api_key: str | None = None
    api_key_source: str = "none"  # "settings" | "env" | "none"
    vision_enabled: bool = False


def resolve_model_config(
    settings: Settings,
    settings_store: SettingsStore | None = None,
) -> ModelRuntimeInfo:
    """统一解析模型配置。

    优先级（API key）：SettingsStore 加密 key > 环境变量 key > 无。
    优先级（模型名）：SettingsStore models（仅用户显式保存时）> env 模型 > 默认。
    provider 选择：显式选择且该 provider 有可用 key > env deepseek > env openai > fake。

    注意：SettingsStore.get_models() 在缺失时会返回默认 ModelSettings，因此
    只有 ``has_models()`` 为真时才算"用户显式选择"，避免默认值被当成选择。
    """
    stored_models = settings_store.get_models() if settings_store is not None else None
    has_models = settings_store is not None and settings_store.has_models()

    # --- OpenAI ---
    openai_key = settings_store.get_api_key("openai") if settings_store is not None else None
    openai_key_source: str = "settings" if openai_key else ("env" if settings.openai_api_key else "none")
    openai_key = openai_key or settings.openai_api_key
    openai_model = (
        stored_models.model if has_models and stored_models.provider == "openai" else settings.openai_model
    )

    # --- DeepSeek ---
    deepseek_key = settings_store.get_api_key("deepseek") if settings_store is not None else None
    deepseek_key_source: str = (
        "settings" if deepseek_key else ("env" if settings.deepseek_api_key else "none")
    )
    deepseek_key = deepseek_key or settings.deepseek_api_key
    deepseek_model = (
        stored_models.model if has_models and stored_models.provider == "deepseek" else settings.deepseek_model
    )

    # 用户在前端显式选择了 provider：仅当该 provider 存在可用 key 时生效，
    # 否则回退到 env 优先级，绝不创建无 key 的真实 provider。
    if has_models and stored_models.provider == "deepseek" and deepseek_key:
        return ModelRuntimeInfo(
            provider="deepseek",
            model=deepseek_model,
            api_key=deepseek_key,
            api_key_source=deepseek_key_source,
            vision_enabled=False,
        )
    if has_models and stored_models.provider == "openai" and openai_key:
        return ModelRuntimeInfo(
            provider="openai",
            model=openai_model,
            api_key=openai_key,
            api_key_source=openai_key_source,
            vision_enabled=_model_supports_vision(
                openai_model,
                base_url=settings.openai_base_url,
            ),
        )

    # 未显式选择（或所选 provider 无 key）：按 env 原有优先级回退
    if deepseek_key:
        return ModelRuntimeInfo(
            provider="deepseek",
            model=settings.deepseek_model,
            api_key=deepseek_key,
            api_key_source=deepseek_key_source,
            vision_enabled=False,
        )
    if openai_key:
        return ModelRuntimeInfo(
            provider="openai",
            model=settings.openai_model,
            api_key=openai_key,
            api_key_source=openai_key_source,
            vision_enabled=_model_supports_vision(
                settings.openai_model,
                base_url=settings.openai_base_url,
            ),
        )
    return ModelRuntimeInfo(
        provider="fake",
        model=settings.openai_model,
        api_key=None,
        api_key_source="none",
        vision_enabled=False,
    )


def create_model_provider(
    settings: Settings,
    settings_store: SettingsStore | None = None,
) -> ModelProvider:
    resolved = resolve_model_config(settings, settings_store)
    if resolved.provider == "deepseek":
        return OpenAICompatibleProvider(
            api_key=resolved.api_key or "",
            model=resolved.model,
            base_url=settings.deepseek_base_url,
            supports_vision=False,
        )
    if resolved.provider == "openai":
        return OpenAICompatibleProvider(
            api_key=resolved.api_key or "",
            model=resolved.model,
            base_url=settings.openai_base_url,
            supports_vision=resolved.vision_enabled,
        )
    return FakeModelProvider()


def create_intent_router(
    settings: Settings,
    *,
    model_provider: ModelProvider,
    tool_registry: "ToolRegistry | None" = None,
) -> IntentRouter:
    if settings.intent_router == "model":
        return ModelIntentRouter(model_provider=model_provider, tool_registry=tool_registry)
    if settings.intent_router == "react":
        return ReActIntentRouter(model_provider=model_provider, tool_registry=tool_registry)
    return HeuristicIntentRouter()
