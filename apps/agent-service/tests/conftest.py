from pathlib import Path

import pytest

from desktopmate_agent.config import Settings


@pytest.fixture
def tmp_allowed_root(tmp_path: Path) -> Path:
    root = tmp_path / "allowed"
    root.mkdir()
    return root


@pytest.fixture
def no_api_settings(tmp_path: Path) -> Settings:
    return Settings(
        trace_dir=tmp_path / "traces",
        allowed_roots=str(tmp_path),
        auth_token="test-token",
        openai_api_key=None,
        deepseek_api_key=None,
    )


@pytest.fixture(autouse=True)
def isolate_session_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """让所有测试的会话库、设置与知识库落在临时目录，避免污染工作区默认路径。"""
    monkeypatch.setenv("DESKTOPMATE_SESSION_DB", str(tmp_path / "sessions.db"))
    monkeypatch.setenv("DESKTOPMATE_SETTINGS_DIR", str(tmp_path / "settings"))
    monkeypatch.setenv("DESKTOPMATE_KNOWLEDGE_DB", str(tmp_path / "knowledge.db"))
    monkeypatch.setenv("DESKTOPMATE_KNOWLEDGE_DIR", str(tmp_path / "knowledge"))
    monkeypatch.setenv("DESKTOPMATE_APP_DATA_DIR", str(tmp_path / "data"))
