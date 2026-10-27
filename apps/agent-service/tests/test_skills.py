"""S4 Skills 安装/导入：zip 校验、Zip Slip 防护、默认 disabled、删除。"""

import io
import json
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

from desktopmate_agent.config import Settings
from desktopmate_agent.main import create_app

AUTH_HEADERS = {"X-DesktopMate-Token": "test-token"}


def make_settings(tmp_path: Path, **overrides) -> Settings:
    defaults = {
        "trace_dir": tmp_path / "traces",
        "settings_dir": tmp_path / "settings",
        "app_data_dir": tmp_path,
        "skills_dir": tmp_path / "skills",
        "auth_token": "test-token",
        "openai_api_key": None,
        "deepseek_api_key": None,
    }
    defaults.update(overrides)
    return Settings(**defaults)


def make_zip(entries: dict[str, bytes | None]) -> bytes:
    """entries: {path: content}；content=None 表示目录。"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in entries.items():
            if content is None:
                archive.writestr(name + "/", "")
            else:
                archive.writestr(name, content)
    return buffer.getvalue()


def skill_zip(name: str = "calendar-helper", version: str = "0.1.0") -> bytes:
    manifest = {
        "name": name,
        "version": version,
        "description": "Manage local calendar reminders",
        "type": "prompt",
        "entry": "README.md",
        "prompt": "你是日程助手：遇到日程/提醒相关请求时，用简洁列表输出并确认时间。",
        "permissions": ["reminders:read", "reminders:write"],
        "risk_level": "medium",
    }
    return make_zip(
        {
            f"{name}/skill.json": json.dumps(manifest).encode(),
            f"{name}/README.md": b"# calendar-helper",
            f"{name}/prompts/": None,
            f"{name}/tools/": None,
        }
    )


def import_zip(client: TestClient, content: bytes, filename: str = "pkg.zip"):
    return client.post(
        "/api/skills/import",
        headers=AUTH_HEADERS,
        files={"file": (filename, content, "application/zip")},
    )


# --- 导入 ---


def test_import_valid_zip_default_disabled(tmp_path):
    client = TestClient(create_app(settings=make_settings(tmp_path)))
    resp = import_zip(client, skill_zip())
    assert resp.status_code == 200, resp.text
    skill = resp.json()["skill"]
    assert skill["name"] == "calendar-helper"
    assert skill["version"] == "0.1.0"
    assert skill["enabled"] is False  # 默认 disabled
    assert skill["risk_level"] == "medium"
    assert skill["requires_permission"] is True

    # 落盘
    manifest = tmp_path / "skills" / "calendar-helper" / "skill.json"
    assert manifest.exists()
    # 列表可见
    listed = client.get("/api/skills", headers=AUTH_HEADERS).json()["skills"]
    assert any(item["name"] == "calendar-helper" and item["enabled"] is False for item in listed)


def test_import_rejects_zip_slip(tmp_path):
    client = TestClient(create_app(settings=make_settings(tmp_path)))
    evil = make_zip({"../evil.txt": b"pwned"})
    resp = import_zip(client, evil)
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "unsafe_path"


def test_import_rejects_absolute_path(tmp_path):
    client = TestClient(create_app(settings=make_settings(tmp_path)))
    evil = make_zip({"/etc/evil.txt": b"pwned"})
    resp = import_zip(client, evil)
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "unsafe_path"


def test_import_rejects_missing_manifest(tmp_path):
    client = TestClient(create_app(settings=make_settings(tmp_path)))
    resp = import_zip(client, make_zip({"random.txt": b"x"}))
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "invalid_skill_package"


def test_import_rejects_invalid_manifest(tmp_path):
    client = TestClient(create_app(settings=make_settings(tmp_path)))
    bad = make_zip({"pkg/skill.json": json.dumps({"name": "bad name!"}).encode()})
    resp = import_zip(client, bad)
    assert resp.status_code == 400


def test_import_rejects_non_zip(tmp_path):
    client = TestClient(create_app(settings=make_settings(tmp_path)))
    resp = import_zip(client, b"not a zip", filename="pkg.txt")
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "unsupported_format"


def test_import_duplicate_conflict(tmp_path):
    client = TestClient(create_app(settings=make_settings(tmp_path)))
    assert import_zip(client, skill_zip()).status_code == 200
    resp = import_zip(client, skill_zip(version="0.2.0"))
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "skill_already_installed"


# --- 详情 / 启用 / 删除 ---


def test_skill_detail_and_enable_persists(tmp_path):
    settings = make_settings(tmp_path)
    client = TestClient(create_app(settings=settings))
    import_zip(client, skill_zip())

    detail = client.get("/api/skills/calendar-helper", headers=AUTH_HEADERS)
    assert detail.status_code == 200
    body = detail.json()
    assert body["permissions"] == ["reminders:read", "reminders:write"]
    assert body["entry"] == "README.md"
    assert "install_path" in body

    patched = client.patch(
        "/api/skills/calendar-helper",
        headers=AUTH_HEADERS,
        json={"enabled": True},
    )
    assert patched.json()["enabled"] is True
    listed = client.get("/api/skills", headers=AUTH_HEADERS).json()["skills"]
    item = next(i for i in listed if i["name"] == "calendar-helper")
    assert item["enabled"] is True

    # 重启后仍启用（settings 持久化）
    client2 = TestClient(create_app(settings=settings))
    item2 = next(
        i
        for i in client2.get("/api/skills", headers=AUTH_HEADERS).json()["skills"]
        if i["name"] == "calendar-helper"
    )
    assert item2["enabled"] is True


def test_delete_skill_removes_package_and_builtin_rejected(tmp_path):
    client = TestClient(create_app(settings=make_settings(tmp_path)))
    import_zip(client, skill_zip())

    resp = client.delete("/api/skills/calendar-helper", headers=AUTH_HEADERS)
    assert resp.json() == {"deleted": True}
    assert not (tmp_path / "skills" / "calendar-helper").exists()
    listed = client.get("/api/skills", headers=AUTH_HEADERS).json()["skills"]
    assert all(item["name"] != "calendar-helper" for item in listed)

    # 内置工具不可删
    builtin = client.get("/api/skills", headers=AUTH_HEADERS).json()["skills"][0]["name"]
    denied = client.delete(f"/api/skills/{builtin}", headers=AUTH_HEADERS)
    assert denied.status_code == 422
    assert denied.json()["error"]["code"] == "builtin_skill"


def test_skills_api_requires_auth(tmp_path):
    client = TestClient(create_app(settings=make_settings(tmp_path)))
    assert client.get("/api/skills").status_code == 401
    assert client.post("/api/skills/import", files={"file": ("a.zip", b"x", "application/zip")}).status_code == 401
    assert client.delete("/api/skills/x").status_code == 401


def test_enabled_prompt_skill_is_injected_into_system_prompt(tmp_path):
    """启用的 prompt skill 会注入 graph 的 system prompt；未启用则不注入。"""
    from desktopmate_agent.agent.graph import AgentGraph
    from desktopmate_agent.main import _load_enabled_skill_prompts
    from desktopmate_agent.models.provider import FakeModelProvider
    from desktopmate_agent.settings.store import SettingsStore
    from desktopmate_agent.skills.store import SkillPackageStore
    from desktopmate_agent.tools.registry import ToolRegistry

    settings = make_settings(tmp_path)
    client = TestClient(create_app(settings=settings))
    response = client.post(
        "/api/skills/import",
        files={"file": ("calendar-helper.zip", skill_zip(), "application/zip")},
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 200
    client.patch("/api/skills/calendar-helper", json={"enabled": True}, headers=AUTH_HEADERS)

    skill_store = SkillPackageStore(settings.skills_dir)
    settings_store = SettingsStore(settings.settings_dir)
    prompts = _load_enabled_skill_prompts(skill_store, settings_store)
    assert prompts == [
        {
            "name": "calendar-helper",
            "prompt": "你是日程助手：遇到日程/提醒相关请求时，用简洁列表输出并确认时间。",
        }
    ]

    graph = AgentGraph(
        model_provider=FakeModelProvider("ok"),
        tool_registry=ToolRegistry(),
        skill_prompts=prompts,
    )
    prompt = graph._build_prompt(user_text="帮我记个日程", memory_context=[], knowledge_context=[])
    assert "<skills>" in prompt
    assert "你是日程助手" in prompt

    # 未启用：另一份全新 settings，无启停记录 → 不注入
    fresh_prompts = _load_enabled_skill_prompts(
        skill_store, SettingsStore(tmp_path / "fresh-settings")
    )
    assert fresh_prompts == []
