"""S1 Runtime 配置源统一：模型配置解析优先级与 provider 刷新行为。"""

from pathlib import Path

from fastapi.testclient import TestClient

from desktopmate_agent.agent.service import AgentService
from desktopmate_agent.config import Settings
from desktopmate_agent.main import create_app
from desktopmate_agent.models.provider import (
    FakeModelProvider,
    OpenAICompatibleProvider,
    create_model_provider,
    resolve_model_config,
)
from desktopmate_agent.settings.store import ModelSettings, SettingsStore
from desktopmate_agent.tools.registry import ToolRegistry
from desktopmate_agent.trace.store import JsonlTraceStore


def make_store(tmp_path: Path) -> SettingsStore:
    return SettingsStore(tmp_path / "settings")


def make_settings(tmp_path: Path, **overrides) -> Settings:
    defaults = {
        "trace_dir": tmp_path / "traces",
        "settings_dir": tmp_path / "settings",
        "auth_token": "test-token",
        "openai_api_key": None,
        "deepseek_api_key": None,
    }
    defaults.update(overrides)
    return Settings(**defaults)


# --- resolve_model_config 优先级 ---


def test_resolve_prefers_settings_key_over_env(tmp_path):
    store = make_store(tmp_path)
    store.save_api_key("openai", "sk-settings")
    info = resolve_model_config(make_settings(tmp_path, openai_api_key="sk-env"), store)
    assert info.provider == "openai"
    assert info.api_key == "sk-settings"
    assert info.api_key_source == "settings"


def test_resolve_falls_back_to_env_after_settings_key_deleted(tmp_path):
    store = make_store(tmp_path)
    store.save_api_key("openai", "sk-settings")
    assert store.delete_api_key("openai") is True
    info = resolve_model_config(make_settings(tmp_path, openai_api_key="sk-env"), store)
    assert info.provider == "openai"
    assert info.api_key == "sk-env"
    assert info.api_key_source == "env"


def test_resolve_returns_fake_without_any_key(tmp_path):
    info = resolve_model_config(make_settings(tmp_path), make_store(tmp_path))
    assert info.provider == "fake"
    assert info.api_key_source == "none"
    assert info.api_key is None


def test_resolve_ignores_explicit_provider_without_key(tmp_path):
    """用户显式选了 deepseek 但未配置任何 key：回退 fake，而非无 key 的真实 provider。"""
    store = make_store(tmp_path)
    store.set_models(ModelSettings(provider="deepseek", model="deepseek-chat"))
    info = resolve_model_config(make_settings(tmp_path), store)
    assert info.provider == "fake"


def test_resolve_honors_explicit_deepseek_with_settings_key(tmp_path):
    store = make_store(tmp_path)
    store.set_models(ModelSettings(provider="deepseek", model="deepseek-chat"))
    store.save_api_key("deepseek", "sk-ds")
    info = resolve_model_config(make_settings(tmp_path, deepseek_api_key="sk-env-ds"), store)
    assert info.provider == "deepseek"
    assert info.model == "deepseek-chat"  # 显式保存的模型名生效
    assert info.api_key == "sk-ds"
    assert info.api_key_source == "settings"


def test_resolve_empty_store_does_not_count_as_explicit_selection(tmp_path):
    """空 store 的 get_models() 返回默认 provider=openai，但不能当作显式选择。"""
    store = make_store(tmp_path)
    assert store.has_models() is False
    info = resolve_model_config(make_settings(tmp_path), store)
    assert info.provider == "fake"


def test_resolve_uses_stored_model_only_for_selected_provider(tmp_path):
    """显式选择 deepseek 时，openai 的 env 回退不能沿用 deepseek 的存储模型。"""
    store = make_store(tmp_path)
    store.set_models(ModelSettings(provider="deepseek", model="deepseek-chat"))
    settings = make_settings(tmp_path, openai_api_key="sk-env", openai_model="gpt-4o")
    info = resolve_model_config(settings, store)
    assert info.provider == "openai"  # 显式 deepseek 无 key，回退 env openai
    assert info.model == "gpt-4o"  # 使用 openai 的 env 模型，而非 deepseek-chat


# --- 保存 key 后立即生效（无需重启） ---


def test_saved_key_takes_effect_on_next_provider_build(tmp_path):
    store = make_store(tmp_path)
    settings = make_settings(tmp_path)

    def build() -> object:
        return create_model_provider(settings, store)

    assert isinstance(build(), FakeModelProvider)
    store.save_api_key("openai", "sk-later")
    provider = build()
    assert isinstance(provider, OpenAICompatibleProvider)
    assert provider.client.api_key == "sk-later"
    assert provider.model == settings.openai_model


def test_create_app_factory_uses_settings_key(tmp_path):
    store = make_store(tmp_path)
    store.save_api_key("deepseek", "sk-app")
    store.set_models(ModelSettings(provider="deepseek", model="deepseek-chat"))
    app = create_app(settings=make_settings(tmp_path))
    provider = app.state.agent_service.provider_factory()
    assert isinstance(provider, OpenAICompatibleProvider)
    assert provider.model == "deepseek-chat"
    assert provider.client.api_key == "sk-app"


# --- refresh_provider 托管行为 ---


class SpyProvider(FakeModelProvider):
    pass


def test_refresh_provider_uses_factory_when_managed(tmp_path):
    service = AgentService(
        model_provider=FakeModelProvider(),
        tool_registry=ToolRegistry(),
        trace_store=JsonlTraceStore(tmp_path / "traces"),
        provider_factory=lambda: SpyProvider(response_text="factory"),
    )
    service.refresh_provider()
    assert isinstance(service.model_provider, SpyProvider)


def test_refresh_provider_does_not_clobber_injected_provider(tmp_path):
    injected = SpyProvider(response_text="injected")
    service = AgentService(
        model_provider=FakeModelProvider(),
        tool_registry=ToolRegistry(),
        trace_store=JsonlTraceStore(tmp_path / "traces"),
        provider_factory=lambda: FakeModelProvider(response_text="factory"),
    )
    service.model_provider = injected  # 测试/调用方注入自定义 provider
    service.refresh_provider()
    assert service.model_provider is injected


def test_refresh_provider_noop_without_factory(tmp_path):
    service = AgentService(
        model_provider=FakeModelProvider(),
        tool_registry=ToolRegistry(),
        trace_store=JsonlTraceStore(tmp_path / "traces"),
    )
    service.refresh_provider()
    assert isinstance(service.model_provider, FakeModelProvider)


# --- GET /api/runtime 模型字段 ---


def test_runtime_reports_model_and_speech(tmp_path):
    settings = make_settings(tmp_path)
    client = TestClient(create_app(settings=settings))
    data = client.get(
        "/api/runtime", headers={"X-DesktopMate-Token": "test-token"}
    ).json()
    assert data["model"]["provider"] == "fake"
    assert data["model"]["api_key_source"] == "none"
    assert data["model"]["vision_enabled"] is False
    assert data["features"]["vision"] is False
    assert data["speech"]["enabled"] is True
    assert data["speech"]["model"] == settings.whisper_model
    assert data["speech"]["device"] == settings.whisper_device
