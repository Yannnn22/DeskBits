from pathlib import Path

from fastapi.testclient import TestClient

from desktopmate_agent.config import Settings
from desktopmate_agent.main import create_app

AUTH_TOKEN = "test-token"
AUTH_HEADERS = {"X-DesktopMate-Token": AUTH_TOKEN}


class FakeKeyTester:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.fail = False

    async def test(self, provider: str, api_key: str) -> None:
        self.calls.append((provider, api_key))
        if self.fail:
            raise RuntimeError("invalid api key")


class FakeModelCatalog:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def list_models(self, provider: str, api_key: str) -> list[str]:
        self.calls.append((provider, api_key))
        return ["gpt-5.4-mini", "gpt-4.1-mini"]


class FakeModelTester:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []
        self.fail = False

    async def test_model(self, provider: str, api_key: str, model: str) -> None:
        self.calls.append((provider, api_key, model))
        if self.fail:
            raise RuntimeError("model unavailable")


def make_settings(tmp_path: Path) -> Settings:
    return Settings(
        trace_dir=tmp_path / "traces",
        auth_token=AUTH_TOKEN,
        openai_api_key=None,
        deepseek_api_key=None,
    )


def test_profile_roundtrip(tmp_path):
    client = TestClient(create_app(settings=make_settings(tmp_path)))

    updated = client.put(
        "/api/settings/profile",
        json={"display_name": "Alice", "data_dir": "/tmp/data"},
        headers=AUTH_HEADERS,
    )
    fetched = client.get("/api/settings/profile", headers=AUTH_HEADERS)

    assert updated.status_code == 200
    assert fetched.json() == {"display_name": "Alice", "data_dir": "/tmp/data"}


def test_models_roundtrip(tmp_path):
    app = create_app(settings=make_settings(tmp_path))
    app.state.settings_store.add_configured_model("deepseek", "deepseek-chat")
    client = TestClient(app)

    updated = client.put(
        "/api/settings/models",
        json={"provider": "deepseek", "model": "deepseek-chat", "temperature": 0.7, "context_length": 64000},
        headers=AUTH_HEADERS,
    )
    fetched = client.get("/api/settings/models", headers=AUTH_HEADERS)

    assert updated.json()["model"] == "deepseek-chat"
    assert fetched.json() == {
        "provider": "deepseek",
        "model": "deepseek-chat",
        "temperature": 0.7,
        "context_length": 64000,
    }


def test_available_models_uses_provider_catalog_without_exposing_key(tmp_path):
    catalog = FakeModelCatalog()
    client = TestClient(
        create_app(
            settings=make_settings(tmp_path),
            key_tester=FakeKeyTester(),
            model_catalog=catalog,
        )
    )
    client.post(
        "/api/settings/api-keys",
        json={"provider": "openai", "api_key": "sk-test-1234567890abcd"},
        headers=AUTH_HEADERS,
    )

    response = client.get("/api/settings/models/available?provider=openai", headers=AUTH_HEADERS)

    assert response.json() == {
        "provider": "openai",
        "models": ["gpt-5.4-mini", "gpt-4.1-mini"],
    }
    assert catalog.calls == [("openai", "sk-test-1234567890abcd")]
    assert "sk-test-1234567890abcd" not in response.text


def test_api_key_never_returns_plaintext(tmp_path):
    client = TestClient(create_app(settings=make_settings(tmp_path), key_tester=FakeKeyTester()))

    saved = client.post(
        "/api/settings/api-keys",
        json={"provider": "openai", "api_key": "sk-test-1234567890abcd"},
        headers=AUTH_HEADERS,
    )
    listed = client.get("/api/settings/api-keys", headers=AUTH_HEADERS)

    assert saved.json()["has_key"] is True
    assert saved.json()["verified_at"] is None
    assert saved.json()["masked"] == "sk-...abcd"
    body = listed.text
    assert "sk-test-1234567890abcd" not in body
    assert listed.json()["keys"][0]["masked"] == "sk-...abcd"


def test_api_key_delete_and_missing_404(tmp_path):
    client = TestClient(create_app(settings=make_settings(tmp_path), key_tester=FakeKeyTester()))
    client.post(
        "/api/settings/api-keys",
        json={"provider": "openai", "api_key": "sk-test-1234567890abcd"},
        headers=AUTH_HEADERS,
    )

    deleted = client.delete("/api/settings/api-keys/openai", headers=AUTH_HEADERS)
    missing = client.delete("/api/settings/api-keys/openai", headers=AUTH_HEADERS)

    assert deleted.json() == {"deleted": True}
    assert missing.status_code == 404


def test_api_key_test_connection(tmp_path):
    tester = FakeKeyTester()
    client = TestClient(
        create_app(
            settings=make_settings(tmp_path),
            key_tester=tester,
            model_catalog=FakeModelCatalog(),
        )
    )
    client.post(
        "/api/settings/api-keys",
        json={"provider": "openai", "api_key": "sk-test-1234567890abcd"},
        headers=AUTH_HEADERS,
    )

    ok = client.post(
        "/api/settings/api-keys/test",
        json={"provider": "openai"},
        headers=AUTH_HEADERS,
    )
    tester.fail = True
    failed = client.post(
        "/api/settings/api-keys/test",
        json={"provider": "openai", "api_key": "sk-bad"},
        headers=AUTH_HEADERS,
    )

    assert ok.json() == {
        "ok": True,
        "provider": "openai",
        "message": "连接成功",
        "models": ["gpt-5.4-mini", "gpt-4.1-mini"],
    }
    assert tester.calls == [("openai", "sk-test-1234567890abcd"), ("openai", "sk-bad")]
    assert failed.json()["ok"] is False

    missing = client.post(
        "/api/settings/api-keys/test",
        json={"provider": "unknown"},
        headers=AUTH_HEADERS,
    )
    assert missing.status_code == 400
    assert missing.json()["error"]["code"] == "api_key_missing"


def test_model_must_pass_real_check_before_it_can_be_selected(tmp_path):
    key_tester = FakeKeyTester()
    model_tester = FakeModelTester()
    app = create_app(
        settings=make_settings(tmp_path),
        key_tester=key_tester,
        model_tester=model_tester,
    )
    client = TestClient(app)
    client.post(
        "/api/settings/api-keys",
        json={"provider": "openai", "api_key": "sk-test-1234567890abcd"},
        headers=AUTH_HEADERS,
    )

    rejected = client.put(
        "/api/settings/models",
        json={"provider": "openai", "model": "gpt-5.4-mini", "temperature": 0.4, "context_length": 128000},
        headers=AUTH_HEADERS,
    )
    tested = client.post(
        "/api/settings/models/test",
        json={"provider": "openai", "model": "gpt-5.4-mini"},
        headers=AUTH_HEADERS,
    )
    selected = client.put(
        "/api/settings/models",
        json={"provider": "openai", "model": "gpt-5.4-mini", "temperature": 0.4, "context_length": 128000},
        headers=AUTH_HEADERS,
    )
    configured = client.get("/api/settings/models/configured", headers=AUTH_HEADERS)

    assert rejected.status_code == 422
    assert rejected.json()["error"]["code"] == "model_not_verified"
    assert tested.json()["ok"] is True
    assert model_tester.calls == [("openai", "sk-test-1234567890abcd", "gpt-5.4-mini")]
    assert selected.status_code == 200
    assert configured.json()["models"][0]["model"] == "gpt-5.4-mini"


def test_key_can_be_saved_before_channel_verification(tmp_path):
    tester = FakeKeyTester()
    tester.fail = True
    client = TestClient(create_app(settings=make_settings(tmp_path), key_tester=tester))

    response = client.post(
        "/api/settings/api-keys",
        json={"provider": "openai", "api_key": "sk-bad"},
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 200
    assert response.json()["verified_at"] is None
    tested = client.post(
        "/api/settings/api-keys/test",
        json={"provider": "openai"},
        headers=AUTH_HEADERS,
    )
    assert tested.json()["ok"] is False


def test_skills_list_and_toggle(tmp_path):
    client = TestClient(create_app(settings=make_settings(tmp_path)))

    listed = client.get("/api/skills", headers=AUTH_HEADERS)
    names = {skill["name"]: skill for skill in listed.json()["skills"]}
    assert names["search_files"]["enabled"] is True

    disabled = client.patch(
        "/api/skills/search_files",
        json={"enabled": False},
        headers=AUTH_HEADERS,
    )
    assert disabled.json()["enabled"] is False

    listed_after = client.get("/api/skills", headers=AUTH_HEADERS).json()["skills"]
    assert next(item for item in listed_after if item["name"] == "search_files")["enabled"] is False


def test_disabled_skill_is_not_callable(tmp_path):
    settings = make_settings(tmp_path)
    client = TestClient(create_app(settings=settings))
    client.patch(
        "/api/skills/search_files",
        json={"enabled": False},
        headers=AUTH_HEADERS,
    )

    response = client.post(
        "/api/chat/invoke",
        json={"text": "/search note"},
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 200
    assert "未注册或已禁用" in response.json()["text"]


def test_skill_toggle_persists_across_restart(tmp_path):
    settings = make_settings(tmp_path)
    client = TestClient(create_app(settings=settings))
    client.patch(
        "/api/skills/knowledge_search",
        json={"enabled": False},
        headers=AUTH_HEADERS,
    )
    client = TestClient(create_app(settings=settings))

    listed = client.get("/api/skills", headers=AUTH_HEADERS).json()["skills"]
    assert next(item for item in listed if item["name"] == "knowledge_search")["enabled"] is False


def test_settings_require_auth(tmp_path):
    client = TestClient(create_app(settings=make_settings(tmp_path)))

    response = client.get("/api/settings/profile")

    assert response.status_code == 401


# --- D3: keys 文件 mtime 缓存 ---


def test_api_key_mtime_cache_avoids_reread_and_invalidates_on_change(tmp_path):
    """key 文件未变时走缓存；文件变化（写 key）后缓存自动失效。"""
    import time

    from desktopmate_agent.settings.store import SettingsStore

    store = SettingsStore(tmp_path / "settings")
    store.save_api_key("openai", "sk-cached-1")
    assert store.get_api_key("openai") == "sk-cached-1"

    # 记录解密次数：读缓存路径不应重新解密（mtime 未变）
    original_decrypt = store._fernet.decrypt
    decrypt_calls = {"n": 0}

    def counting_decrypt(token):
        decrypt_calls["n"] += 1
        return original_decrypt(token)

    store._fernet.decrypt = counting_decrypt  # type: ignore[method-assign]
    assert store.get_api_key("openai") == "sk-cached-1"
    assert store.get_api_key("openai") == "sk-cached-1"
    assert decrypt_calls["n"] == 0  # 纯缓存命中，无解密

    # 写新 key → mtime 变化 → 缓存失效 → 重新解密读取
    time.sleep(0.01)  # 保证 mtime 可区分（macOS APFS 纳秒精度，保险起见）
    store.save_api_key("openai", "sk-cached-2")
    assert store.get_api_key("openai") == "sk-cached-2"
    assert decrypt_calls["n"] == 1  # 恰好一次解密
