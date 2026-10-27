from pathlib import Path

from fastapi.testclient import TestClient

from desktopmate_agent.config import Settings
from desktopmate_agent.main import create_app

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


def test_runtime_requires_auth_and_reports_features(tmp_path):
    client = TestClient(create_app(settings=make_settings(tmp_path)))

    # H5：/api/runtime 需要鉴权（泄露模型配置 + key 来源元数据）
    unauthorized = client.get("/api/runtime")
    assert unauthorized.status_code == 401

    response = client.get("/api/runtime", headers=AUTH_HEADERS)

    assert response.status_code == 200
    data = response.json()
    assert data["service"] == "desktopmate-agent-service"
    assert data["version"]
    assert data["auth_enabled"] is True
    assert data["features"]["websocket_chat"] is True
    assert data["features"]["sessions"] is True
    assert data["features"]["settings"] is True
    assert data["features"]["voice"] is True


def test_root_returns_api_index(tmp_path):
    client = TestClient(create_app(settings=make_settings(tmp_path)))

    response = client.get("/")

    assert response.status_code == 200
    data = response.json()
    assert data["service"] == "desktopmate-agent-service"
    assert data["api"]["health"] == "/api/health"
    assert data["api"]["sessions"] == "/api/sessions"


def test_health_reports_auth_enabled(tmp_path):
    client = TestClient(create_app(settings=make_settings(tmp_path)))

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["auth_enabled"] is True


def test_tools_requires_token(tmp_path):
    client = TestClient(create_app(settings=make_settings(tmp_path)))

    missing = client.get("/api/tools")

    assert missing.status_code == 401
    assert missing.json() == {
        "error": {"code": "unauthorized", "message": "Unauthorized", "details": {}}
    }


def test_tools_lists_registered_tools(tmp_path):
    client = TestClient(create_app(settings=make_settings(tmp_path)))

    response = client.get("/api/tools", headers=AUTH_HEADERS)

    assert response.status_code == 200
    tools = response.json()["tools"]
    names = {tool["name"] for tool in tools}
    assert "search_files" in names
    assert "knowledge_search" in names
    search_files = next(tool for tool in tools if tool["name"] == "search_files")
    assert search_files["risk_level"] == "read"
    assert search_files["enabled"] is True
    assert "type" in search_files["args_schema"]


def test_validation_errors_use_unified_format(tmp_path):
    client = TestClient(create_app(settings=make_settings(tmp_path)))

    response = client.post(
        "/api/chat/invoke",
        json={"text": ""},
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 422
    body = response.json()["error"]
    assert body["code"] == "validation_error"
    assert body["message"]
    assert body["details"]["errors"]


def test_cors_preflight_allows_listed_origin(tmp_path):
    client = TestClient(
        create_app(
            settings=make_settings(
                tmp_path,
                allowed_origins="http://localhost:5173",
            )
        )
    )

    response = client.options(
        "/api/chat/invoke",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "POST",
        },
    )

    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == "http://localhost:5173"


def test_cors_disabled_when_no_origins_configured(tmp_path):
    client = TestClient(create_app(settings=make_settings(tmp_path)))

    response = client.options(
        "/api/chat/invoke",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "POST",
        },
    )

    assert "access-control-allow-origin" not in response.headers
