from functools import lru_cache
import os
from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    env: str = Field(default="development", alias="DESKTOPMATE_ENV")
    host: str = Field(default="127.0.0.1", alias="DESKTOPMATE_HOST")
    port: int = Field(default=0, alias="DESKTOPMATE_PORT")
    auth_token: str | None = Field(default=None, alias="DESKTOPMATE_AUTH_TOKEN")
    allowed_origins: str = Field(default="", alias="DESKTOPMATE_ALLOWED_ORIGINS")
    app_data_dir: Path = Field(
        default_factory=lambda: Path.home() / ".desktopmate",
        alias="DESKTOPMATE_APP_DATA_DIR",
    )
    trace_dir: Path = Field(
        default_factory=lambda: Path.home() / ".desktopmate" / "traces",
        alias="DESKTOPMATE_TRACE_DIR",
    )
    trace_retention_days: int = Field(default=7, ge=1, alias="DESKTOPMATE_TRACE_RETENTION_DAYS")
    draft_dir: Path = Field(
        default_factory=lambda: Path.home() / ".desktopmate" / "drafts",
        alias="DESKTOPMATE_DRAFT_DIR",
    )
    memory_dir: Path = Field(
        default_factory=lambda: Path.home() / ".desktopmate" / "memory",
        alias="DESKTOPMATE_MEMORY_DIR",
    )
    memory_backend: str = Field(default="sqlite", alias="DESKTOPMATE_MEMORY_BACKEND")
    redis_url: str = Field(default="redis://127.0.0.1:16379/0", alias="DESKTOPMATE_REDIS_URL")
    redis_memory_key_prefix: str = Field(
        default="desktopmate:memory",
        alias="DESKTOPMATE_REDIS_MEMORY_KEY_PREFIX",
    )
    knowledge_dir: Path = Field(
        default_factory=lambda: Path.home() / ".desktopmate" / "knowledge",
        alias="DESKTOPMATE_KNOWLEDGE_DIR",
    )
    knowledge_db_path: Path = Field(
        default_factory=lambda: Path.home() / ".desktopmate" / "desktopmate.db",
        alias="DESKTOPMATE_KNOWLEDGE_DB",
    )
    uploads_dir: Path = Field(
        default_factory=lambda: Path.home() / ".desktopmate" / "uploads",
        alias="DESKTOPMATE_UPLOADS_DIR",
    )
    max_upload_bytes: int = Field(
        default=10 * 1024 * 1024,
        ge=1,
        alias="DESKTOPMATE_MAX_UPLOAD_BYTES",
    )
    session_db_path: Path = Field(
        default_factory=lambda: Path.home() / ".desktopmate" / "desktopmate.db",
        alias="DESKTOPMATE_SESSION_DB",
    )
    settings_dir: Path = Field(
        default_factory=lambda: Path.home() / ".desktopmate" / "settings",
        alias="DESKTOPMATE_SETTINGS_DIR",
    )
    skills_dir: Path = Field(
        default_factory=lambda: Path.home() / ".desktopmate" / "skills",
        alias="DESKTOPMATE_SKILLS_DIR",
    )
    memory_user_id: str = Field(default="default_user", alias="DESKTOPMATE_MEMORY_USER_ID")
    memory_project_id: str | None = Field(default=None, alias="DESKTOPMATE_MEMORY_PROJECT_ID")
    memory_context_limit: int = Field(default=8, ge=1, le=50, alias="DESKTOPMATE_MEMORY_CONTEXT_LIMIT")
    context_compression_enabled: bool = Field(
        default=False,
        alias="DESKTOPMATE_CONTEXT_COMPRESSION_ENABLED",
    )
    context_compression_threshold: int = Field(
        default=12,
        ge=2,
        alias="DESKTOPMATE_CONTEXT_COMPRESSION_THRESHOLD",
    )
    auto_memory_candidates: bool = Field(
        default=False,
        alias="DESKTOPMATE_MEMORY_AUTO_CANDIDATES",
    )
    auto_memory_candidates_limit: int = Field(
        default=3,
        ge=1,
        le=10,
        alias="DESKTOPMATE_MEMORY_AUTO_CANDIDATES_LIMIT",
    )
    embedding_provider: str = Field(default="fake", alias="DESKTOPMATE_EMBEDDING_PROVIDER")
    embedding_dimensions: int = Field(default=128, ge=1, alias="DESKTOPMATE_EMBEDDING_DIMENSIONS")
    embedding_api_key: str | None = Field(default=None, alias="DESKTOPMATE_EMBEDDING_API_KEY")
    embedding_base_url: str | None = Field(default=None, alias="DESKTOPMATE_EMBEDDING_BASE_URL")
    embedding_model: str = Field(default="text-embedding-3-small", alias="DESKTOPMATE_EMBEDDING_MODEL")
    milvus_enabled: bool = Field(default=False, alias="DESKTOPMATE_MILVUS_ENABLED")
    milvus_uri: str = Field(default="http://127.0.0.1:19530", alias="DESKTOPMATE_MILVUS_URI")
    milvus_collection_name: str = Field(
        default="desktopmate_knowledge",
        alias="DESKTOPMATE_MILVUS_COLLECTION_NAME",
    )
    allowed_roots: str = Field(default="", alias="DESKTOPMATE_ALLOWED_ROOTS")
    max_read_bytes: int = Field(default=5_000_000, ge=1, alias="DESKTOPMATE_MAX_READ_FILE_BYTES")
    max_search_files: int = Field(default=100_000, ge=1, alias="DESKTOPMATE_MAX_SEARCH_FILES")
    intent_router: str = Field(default="react", alias="DESKTOPMATE_INTENT_ROUTER")
    openai_api_key: str | None = Field(default=None, alias="OPENAI_API_KEY")
    openai_base_url: str | None = Field(default=None, alias="OPENAI_BASE_URL")
    openai_model: str = Field(default="gpt-4.1-mini", alias="OPENAI_MODEL")
    deepseek_api_key: str | None = Field(default=None, alias="DEEPSEEK_API_KEY")
    deepseek_base_url: str = Field(default="https://api.deepseek.com", alias="DEEPSEEK_BASE_URL")
    deepseek_model: str = Field(default="deepseek-v4-flash", alias="DEEPSEEK_MODEL")
    tavily_api_key: str | None = Field(default=None, alias="TAVILY_API_KEY")
    tavily_base_url: str = Field(default="https://api.tavily.com/search", alias="TAVILY_BASE_URL")
    whisper_model: str = Field(default="small", alias="DESKTOPMATE_WHISPER_MODEL")
    whisper_device: str = Field(default="cpu", alias="DESKTOPMATE_WHISPER_DEVICE")
    whisper_lang: str = Field(default="auto", alias="DESKTOPMATE_WHISPER_LANG")
    whisper_model_dir: Path | None = Field(
        default=None,
        alias="DESKTOPMATE_WHISPER_MODEL_DIR",
    )

    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)

    @model_validator(mode="before")
    @classmethod
    def inherit_data_directory(cls, values: object) -> object:
        """Keep default persistence paths together when only the data root is overridden."""
        if not isinstance(values, dict):
            return values
        root = values.get("app_data_dir") or values.get("DESKTOPMATE_APP_DATA_DIR")
        if not root:
            return values
        root_path = Path(root)
        defaults = {
            "trace_dir": "traces",
            "draft_dir": "drafts",
            "memory_dir": "memory",
            "knowledge_dir": "knowledge",
            "knowledge_db_path": "desktopmate.db",
            "uploads_dir": "uploads",
            "session_db_path": "desktopmate.db",
            "settings_dir": "settings",
            "skills_dir": "skills",
        }
        aliases = {
            "trace_dir": "DESKTOPMATE_TRACE_DIR",
            "draft_dir": "DESKTOPMATE_DRAFT_DIR",
            "memory_dir": "DESKTOPMATE_MEMORY_DIR",
            "knowledge_dir": "DESKTOPMATE_KNOWLEDGE_DIR",
            "knowledge_db_path": "DESKTOPMATE_KNOWLEDGE_DB",
            "uploads_dir": "DESKTOPMATE_UPLOADS_DIR",
            "session_db_path": "DESKTOPMATE_SESSION_DB",
            "settings_dir": "DESKTOPMATE_SETTINGS_DIR",
            "skills_dir": "DESKTOPMATE_SKILLS_DIR",
        }
        for field_name, relative_path in defaults.items():
            if field_name not in values and aliases[field_name] not in values:
                values[field_name] = root_path / relative_path
        return values

    @property
    def allowed_root_paths(self) -> list[Path]:
        return [Path(item).expanduser().resolve() for item in self.allowed_roots.split(os.pathsep) if item]

    @property
    def allowed_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.allowed_origins.split(",") if origin.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
