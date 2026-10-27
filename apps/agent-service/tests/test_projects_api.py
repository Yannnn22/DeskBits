"""项目 API（左侧栏项目列表 + 项目内会话）测试。"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from desktopmate_agent.config import Settings
from desktopmate_agent.main import create_app

AUTH = {"X-DesktopMate-Token": "t"}


def make_client(tmp_path: Path) -> TestClient:
    settings = Settings(
        trace_dir=tmp_path / "traces",
        settings_dir=tmp_path / "settings",
        app_data_dir=tmp_path,
        uploads_dir=tmp_path / "uploads",
        auth_token="t",
        openai_api_key=None,
        deepseek_api_key=None,
        allowed_roots=str(tmp_path),
    )
    return TestClient(create_app(settings=settings))


def test_add_and_list_project(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    proj = tmp_path / "demo"
    proj.mkdir()
    (proj / "README.md").write_text("# Demo\n", encoding="utf-8")

    # 添加
    r = client.post("/api/projects", headers=AUTH, json={"name": "Demo", "path": str(proj)})
    assert r.status_code == 200
    project = r.json()["project"]
    assert project["name"] == "Demo"
    assert Path(project["path"]).is_dir()

    # 列表
    body = client.get("/api/projects", headers=AUTH).json()
    assert any(item["name"] == "Demo" for item in body["projects"])

    # 重复添加 → 409
    r_dup = client.post("/api/projects", headers=AUTH, json={"name": "Demo2", "path": str(proj)})
    assert r_dup.status_code == 409


def test_project_path_outside_roots_rejected(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    r = client.post(
        "/api/projects",
        headers=AUTH,
        json={"name": "Home", "path": str(Path.home())},
    )
    assert r.status_code == 403


def test_project_sessions_roundtrip(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    proj = tmp_path / "demo"
    proj.mkdir()
    project = client.post(
        "/api/projects", headers=AUTH, json={"name": "Demo", "path": str(proj)}
    ).json()["project"]
    project_id = project["id"]

    # 创建项目会话
    r = client.post(f"/api/projects/{project_id}/sessions", headers=AUTH)
    assert r.status_code == 200
    session = r.json()["session"]
    assert session["project_id"] == project_id

    # 列出项目会话
    body = client.get(f"/api/projects/{project_id}/sessions", headers=AUTH).json()
    assert len(body["sessions"]) >= 1
    assert body["sessions"][0]["id"] == session["id"]

    # 不存在的项目 → 404
    assert client.get("/api/projects/nope/sessions", headers=AUTH).status_code == 404


def test_delete_project(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    proj = tmp_path / "demo"
    proj.mkdir()
    project = client.post(
        "/api/projects", headers=AUTH, json={"name": "Demo", "path": str(proj)}
    ).json()["project"]

    assert client.delete(f"/api/projects/{project['id']}", headers=AUTH).status_code == 200
    assert client.delete(f"/api/projects/{project['id']}", headers=AUTH).status_code == 404
