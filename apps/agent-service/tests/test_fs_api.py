"""文件系统 API（2.1 workspace 增强）测试。"""

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


def test_fs_roots(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    body = client.get("/api/fs/roots", headers=AUTH).json()
    assert len(body["roots"]) >= 1
    assert any(str(tmp_path.resolve()) in item["path"] for item in body["roots"])


def test_fs_tree_lists_files_and_ignores_noise(tmp_path: Path) -> None:
    proj = tmp_path / "demo"
    proj.mkdir()
    (proj / "graph.py").write_text("x = 1\n", encoding="utf-8")
    (proj / "README.md").write_text("# Demo\n", encoding="utf-8")
    (proj / "src").mkdir()
    (proj / "node_modules").mkdir()
    (proj / ".git").mkdir()

    client = make_client(tmp_path)
    body = client.get(f"/api/fs/tree?path={proj}", headers=AUTH).json()
    names = [f["name"] for f in body["files"]] + [d["name"] for d in body["dirs"]]
    assert "graph.py" in names
    assert "README.md" in names
    assert "src" in names
    assert "node_modules" not in names
    assert ".git" not in names


def test_fs_tree_subdirectory(tmp_path: Path) -> None:
    proj = tmp_path / "demo"
    (proj / "src" / "agent").mkdir(parents=True)
    (proj / "src" / "agent" / "intent.py").write_text("def route(): pass\n", encoding="utf-8")

    client = make_client(tmp_path)
    body = client.get(f"/api/fs/tree?path={proj / 'src' / 'agent'}", headers=AUTH).json()
    assert [f["name"] for f in body["files"]] == ["intent.py"]


def test_fs_context_builds_project_summary(tmp_path: Path) -> None:
    proj = tmp_path / "demo"
    (proj / "src").mkdir(parents=True)
    (proj / "src" / "main.py").write_text("if __name__ == '__main__':\n    pass\n", encoding="utf-8")
    (proj / "README.md").write_text("# Demo Project\n\n说明文字。\n", encoding="utf-8")
    (proj / "config.json").write_text("{}", encoding="utf-8")

    client = make_client(tmp_path)
    ctx = client.get(f"/api/fs/context?path={proj}", headers=AUTH).json()
    assert "Demo Project" in ctx["readme"]
    assert "main.py" in ctx["tree"]
    assert "src/" in ctx["tree"]
    assert ctx["total_files"] >= 3
    assert any(item["ext"] == ".py" for item in ctx["extensions"])


def test_fs_rejects_path_outside_roots(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    response = client.get(f"/api/fs/tree?path={Path.home()}", headers=AUTH)
    assert response.status_code == 403


def test_fs_tree_requires_directory(tmp_path: Path) -> None:
    file_path = tmp_path / "plain.txt"
    file_path.write_text("x", encoding="utf-8")

    client = make_client(tmp_path)
    response = client.get(f"/api/fs/tree?path={file_path}", headers=AUTH)
    assert response.status_code == 404


def test_fs_read_file(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    target = tmp_path / "notes.md"
    target.write_text("# 笔记\n\n内容。\n", encoding="utf-8")

    body = client.get(f"/api/fs/read?path={target}", headers=AUTH).json()
    assert body["name"] == "notes.md"
    assert "内容" in body["content"]


def test_fs_read_rejects_directory(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    response = client.get(f"/api/fs/read?path={tmp_path}", headers=AUTH)
    assert response.status_code == 404


def test_fs_write_file(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    target = tmp_path / "demo" / "new.txt"
    r = client.post(
        "/api/fs/write",
        headers=AUTH,
        json={"path": str(target), "content": "hello world\n"},
    )
    assert r.status_code == 200
    assert target.read_text(encoding="utf-8") == "hello world\n"

    # 读回验证
    body = client.get(f"/api/fs/read?path={target}", headers=AUTH).json()
    assert body["content"] == "hello world\n"


def test_fs_write_outside_roots_rejected(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    r = client.post(
        "/api/fs/write",
        headers=AUTH,
        json={"path": str(Path.home() / "evil.txt"), "content": "x"},
    )
    assert r.status_code == 403
