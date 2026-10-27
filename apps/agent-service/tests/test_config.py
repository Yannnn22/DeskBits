import os
from pathlib import Path

from desktopmate_agent.config import Settings, get_settings


def test_settings_can_be_constructed_by_field_name():
    settings = Settings(env="test", host="0.0.0.0")

    assert settings.env == "test"
    assert settings.host == "0.0.0.0"


def test_settings_can_be_constructed_by_env_alias():
    settings = Settings(DESKTOPMATE_ALLOWED_ROOTS="/tmp/desktopmate")

    assert settings.allowed_roots == "/tmp/desktopmate"


def test_allowed_root_paths_uses_os_pathsep(tmp_path: Path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    settings = Settings(allowed_roots=f"{first}{os.pathsep}{second}")

    assert settings.allowed_root_paths == [first.resolve(), second.resolve()]


def test_milvus_settings_have_safe_defaults():
    settings = Settings(openai_api_key=None, deepseek_api_key=None)

    assert settings.milvus_enabled is False
    assert settings.milvus_uri == "http://127.0.0.1:19530"
    assert settings.milvus_collection_name == "desktopmate_knowledge"
    assert settings.embedding_dimensions == 128


def test_embedding_settings_default_to_fake_provider():
    settings = Settings(openai_api_key=None, deepseek_api_key=None)

    assert settings.embedding_provider == "fake"
    assert settings.embedding_model == "text-embedding-3-small"
    assert settings.embedding_api_key is None
    assert settings.embedding_base_url is None


def test_memory_backend_settings_default_to_sqlite():
    settings = Settings(openai_api_key=None, deepseek_api_key=None)

    assert settings.memory_backend == "sqlite"
    assert settings.redis_url == "redis://127.0.0.1:16379/0"
    assert settings.redis_memory_key_prefix == "desktopmate:memory"
    assert settings.intent_router == "react"


def test_single_desktopmate_db_by_default(monkeypatch):
    monkeypatch.delenv("DESKTOPMATE_SESSION_DB", raising=False)
    monkeypatch.delenv("DESKTOPMATE_KNOWLEDGE_DB", raising=False)
    monkeypatch.delenv("DESKTOPMATE_APP_DATA_DIR", raising=False)

    settings = Settings(openai_api_key=None, deepseek_api_key=None)

    # 默认 sessions/knowledge/memory 共用应用数据目录下的 desktopmate.db
    assert settings.session_db_path == settings.knowledge_db_path
    assert settings.session_db_path.name == "desktopmate.db"
    assert settings.session_db_path.parent == settings.app_data_dir


PERSISTENCE_PATH_ENV_NAMES = (
    "DESKTOPMATE_TRACE_DIR",
    "DESKTOPMATE_DRAFT_DIR",
    "DESKTOPMATE_MEMORY_DIR",
    "DESKTOPMATE_KNOWLEDGE_DIR",
    "DESKTOPMATE_KNOWLEDGE_DB",
    "DESKTOPMATE_UPLOADS_DIR",
    "DESKTOPMATE_SESSION_DB",
    "DESKTOPMATE_SETTINGS_DIR",
    "DESKTOPMATE_SKILLS_DIR",
)


def test_custom_app_data_directory_cascades_to_default_persistence_paths(tmp_path, monkeypatch):
    for env_name in PERSISTENCE_PATH_ENV_NAMES:
        monkeypatch.delenv(env_name, raising=False)
    settings = Settings(
        _env_file=None,
        app_data_dir=tmp_path,
        openai_api_key=None,
        deepseek_api_key=None,
    )

    assert settings.trace_dir == tmp_path / "traces"
    assert settings.knowledge_dir == tmp_path / "knowledge"
    assert settings.session_db_path == tmp_path / "desktopmate.db"
    assert settings.knowledge_db_path == tmp_path / "desktopmate.db"
    assert settings.uploads_dir == tmp_path / "uploads"


def test_explicit_persistence_path_overrides_custom_app_data_directory(tmp_path, monkeypatch):
    for env_name in PERSISTENCE_PATH_ENV_NAMES:
        monkeypatch.delenv(env_name, raising=False)
    session_db = tmp_path / "separate" / "sessions.db"
    settings = Settings(
        _env_file=None,
        app_data_dir=tmp_path,
        session_db_path=session_db,
        openai_api_key=None,
        deepseek_api_key=None,
    )

    assert settings.session_db_path == session_db
    assert settings.knowledge_db_path == tmp_path / "desktopmate.db"


def test_security_settings_default_to_fail_closed():
    settings = Settings(_env_file=None, openai_api_key=None, deepseek_api_key=None)

    assert settings.allowed_roots == ""
    assert settings.allowed_root_paths == []
    assert settings.auth_token is None
    assert settings.allowed_origins == ""
    assert settings.allowed_origin_list == []
    assert settings.max_read_bytes == 5_000_000
    assert settings.max_search_files == 100_000
    assert settings.trace_retention_days == 7


def test_allowed_origin_list_parses_comma_separated_values():
    settings = Settings(
        allowed_origins="http://localhost:5173, app://desktopmate",
        openai_api_key=None,
        deepseek_api_key=None,
    )

    assert settings.allowed_origin_list == ["http://localhost:5173", "app://desktopmate"]


def test_get_settings_cache_can_be_cleared(monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setenv("DESKTOPMATE_ENV", "first")
    first = get_settings()

    monkeypatch.setenv("DESKTOPMATE_ENV", "second")
    cached = get_settings()
    get_settings.cache_clear()
    refreshed = get_settings()

    assert cached is first
    assert refreshed.env == "second"

    get_settings.cache_clear()
