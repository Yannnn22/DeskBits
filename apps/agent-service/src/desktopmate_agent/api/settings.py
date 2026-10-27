"""Settings 与 Skills API：profile / models / api-keys / skills。"""

import asyncio
from typing import Protocol

from fastapi import APIRouter, Depends, HTTPException
from openai import AsyncOpenAI
from pydantic import BaseModel, Field

from desktopmate_agent.api.routes import require_auth
from desktopmate_agent.config import Settings
from desktopmate_agent.settings.store import (
    ApiKeyRecord,
    ConfiguredModel,
    ModelSettings,
    ProfileSettings,
    SettingsStore,
)
from desktopmate_agent.tools.web_search import test_tavily_api_key

_PROVIDER_BASE_URLS = {
    "openai": None,
    "deepseek": "https://api.deepseek.com",
}
_API_KEY_PROVIDERS = frozenset({"openai", "deepseek", "tavily"})


class ApiKeyTester(Protocol):
    async def test(self, provider: str, api_key: str) -> None:
        raise NotImplementedError


class ModelCatalog(Protocol):
    async def list_models(self, provider: str, api_key: str) -> list[str]:
        raise NotImplementedError


class ModelTester(Protocol):
    async def test_model(self, provider: str, api_key: str, model: str) -> None:
        raise NotImplementedError


class OpenAIKeyTester:
    def __init__(self, *, settings: Settings | None = None) -> None:
        self._settings = settings

    async def test(self, provider: str, api_key: str) -> None:
        client = self._client(provider, api_key)
        await client.models.list()

    async def list_models(self, provider: str, api_key: str) -> list[str]:
        response = await self._client(provider, api_key).models.list()
        return sorted({str(item.id) for item in response.data if getattr(item, "id", None)})

    async def test_model(self, provider: str, api_key: str, model: str) -> None:
        await self._client(provider, api_key).chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "Reply with OK."}],
            max_tokens=1,
        )

    def _client(self, provider: str, api_key: str) -> AsyncOpenAI:
        base_url = _PROVIDER_BASE_URLS.get(provider)
        if self._settings is not None:
            if provider == "deepseek":
                base_url = self._settings.deepseek_base_url
            elif provider == "openai":
                base_url = self._settings.openai_base_url
        return AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=15.0)


class ApiKeyPayload(BaseModel):
    provider: str = Field(min_length=1, max_length=100)
    api_key: str = Field(min_length=1, max_length=500)


class ApiKeyTestPayload(BaseModel):
    provider: str = Field(min_length=1, max_length=100)
    api_key: str | None = Field(default=None, max_length=500)


class ModelTestPayload(BaseModel):
    provider: str = Field(min_length=1, max_length=100)
    model: str = Field(min_length=1, max_length=100)


def create_settings_router(
    settings_store: SettingsStore,
    *,
    key_tester: ApiKeyTester | None = None,
    model_catalog: ModelCatalog | None = None,
    model_tester: ModelTester | None = None,
    settings: Settings | None = None,
) -> APIRouter:
    router = APIRouter()
    active_key_tester = key_tester or OpenAIKeyTester(settings=settings)
    active_model_catalog = model_catalog or OpenAIKeyTester(settings=settings)
    active_model_tester = model_tester or OpenAIKeyTester(settings=settings)

    def resolve_api_key(provider: str) -> str | None:
        saved_key = settings_store.get_api_key(provider)
        if saved_key:
            return saved_key
        if settings is None:
            return None
        if provider == "openai":
            return settings.openai_api_key
        if provider == "deepseek":
            return settings.deepseek_api_key
        return None

    def ensure_model_provider(provider: str) -> None:
        if provider not in _PROVIDER_BASE_URLS:
            raise HTTPException(
                status_code=400,
                detail={"code": "unsupported_provider", "message": "该提供商暂不支持模型配置"},
            )

    def ensure_api_key_provider(provider: str) -> None:
        if provider not in _API_KEY_PROVIDERS:
            raise HTTPException(
                status_code=400,
                detail={"code": "unsupported_provider", "message": "该提供商暂不支持 API Key 配置"},
            )

    # --- Profile ---

    @router.get("/api/settings/profile")
    def get_profile(_: None = Depends(require_auth)) -> ProfileSettings:
        return settings_store.get_profile()

    @router.put("/api/settings/profile")
    def put_profile(
        payload: ProfileSettings,
        _: None = Depends(require_auth),
    ) -> ProfileSettings:
        return settings_store.set_profile(payload)

    # --- Models ---

    @router.get("/api/settings/models")
    def get_models(_: None = Depends(require_auth)) -> ModelSettings:
        return settings_store.get_models()

    @router.put("/api/settings/models")
    def put_models(
        payload: ModelSettings,
        _: None = Depends(require_auth),
    ) -> ModelSettings:
        ensure_model_provider(payload.provider)
        if not settings_store.has_configured_model(payload.provider, payload.model):
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "model_not_verified",
                    "message": "请先测试模型并添加到可用模型列表。",
                },
            )
        return settings_store.set_models(payload)

    @router.get("/api/settings/models/configured")
    def list_configured_models(_: None = Depends(require_auth)) -> dict[str, list[ConfiguredModel]]:
        return {"models": settings_store.list_configured_models()}

    @router.get("/api/settings/models/available")
    async def get_available_models(
        provider: str | None = None,
        _: None = Depends(require_auth),
    ) -> dict[str, object]:
        selected_provider = provider or settings_store.get_models().provider
        ensure_model_provider(selected_provider)
        api_key = resolve_api_key(selected_provider)
        if not api_key:
            raise HTTPException(
                status_code=400,
                detail={"code": "api_key_missing", "message": "请先保存该提供商的 API 密钥"},
            )
        try:
            models = await active_model_catalog.list_models(selected_provider, api_key)
        except Exception as exc:
            return {"provider": selected_provider, "models": [], "error": str(exc)}
        return {"provider": selected_provider, "models": models}

    @router.post("/api/settings/models/test")
    async def test_model(
        payload: ModelTestPayload,
        _: None = Depends(require_auth),
    ) -> dict[str, object]:
        ensure_model_provider(payload.provider)
        api_key = resolve_api_key(payload.provider)
        if not api_key:
            raise HTTPException(
                status_code=400,
                detail={"code": "api_key_missing", "message": "请先保存并验证该提供商的 API 密钥"},
            )
        try:
            await active_model_tester.test_model(payload.provider, api_key, payload.model)
        except Exception as exc:
            return {
                "ok": False,
                "provider": payload.provider,
                "model": payload.model,
                "message": str(exc),
            }
        configured = settings_store.add_configured_model(payload.provider, payload.model)
        if settings_store.get_api_key(payload.provider):
            settings_store.mark_api_key_verified(payload.provider)
        return {
            "ok": True,
            "provider": payload.provider,
            "model": payload.model,
            "message": "模型测试通过，已添加到对话模型列表。",
            "configured_model": configured,
        }

    @router.delete("/api/settings/models/configured")
    def remove_configured_model(
        provider: str,
        model: str,
        _: None = Depends(require_auth),
    ) -> dict[str, bool]:
        current = settings_store.get_models()
        if current.provider == provider and current.model == model:
            raise HTTPException(
                status_code=409,
                detail={"code": "active_model", "message": "当前正在使用的模型不能移除。"},
            )
        if not settings_store.remove_configured_model(provider, model):
            raise HTTPException(
                status_code=404,
                detail={"code": "model_not_found", "message": "模型不在已配置列表中。"},
            )
        return {"deleted": True}

    # --- API Keys ---

    @router.get("/api/settings/api-keys")
    def list_api_keys(_: None = Depends(require_auth)) -> dict[str, object]:
        return {"keys": settings_store.list_api_keys()}

    @router.post("/api/settings/api-keys")
    async def save_api_key(
        payload: ApiKeyPayload,
        _: None = Depends(require_auth),
    ) -> ApiKeyRecord:
        ensure_api_key_provider(payload.provider)
        return settings_store.save_api_key(payload.provider, payload.api_key)

    @router.delete("/api/settings/api-keys/{provider}")
    def delete_api_key(
        provider: str,
        _: None = Depends(require_auth),
    ) -> dict[str, bool]:
        deleted = settings_store.delete_api_key(provider)
        if not deleted:
            raise HTTPException(
                status_code=404,
                detail={"code": "api_key_not_found", "message": f"提供商 {provider} 未保存密钥"},
            )
        return {"deleted": True}

    @router.post("/api/settings/api-keys/test")
    async def test_api_key(
        payload: ApiKeyTestPayload,
        _: None = Depends(require_auth),
    ) -> dict[str, object]:
        api_key = payload.api_key or settings_store.get_api_key(payload.provider)
        if not api_key:
            api_key = resolve_api_key(payload.provider)
        if not api_key:
            raise HTTPException(
                status_code=400,
                detail={"code": "api_key_missing", "message": "请先保存密钥或提供 api_key"},
            )
        ensure_api_key_provider(payload.provider)
        try:
            if payload.provider == "tavily":
                search_url = settings.tavily_base_url if settings is not None else None
                await asyncio.to_thread(test_tavily_api_key, api_key, search_url or "https://api.tavily.com/search")
            else:
                await active_key_tester.test(payload.provider, api_key)
        except Exception as exc:
            return {"ok": False, "provider": payload.provider, "message": str(exc)}
        if payload.api_key is None and settings_store.get_api_key(payload.provider):
            settings_store.mark_api_key_verified(payload.provider)
        if payload.provider == "tavily":
            models = []
        else:
            try:
                models = await active_model_catalog.list_models(payload.provider, api_key)
            except Exception:
                models = []
        return {
            "ok": True,
            "provider": payload.provider,
            "message": "连接成功",
            "models": models,
        }

    return router
