"""本地设置存储。

- settings.json: profile / models / skills 启用状态，明文可读。
- keys.enc: 提供商 API 密钥，用 Fernet 加密；密钥文件 key 权限 0600，不入 git。
"""

import json
import os
from datetime import UTC, datetime
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from pydantic import BaseModel, Field


class ProfileSettings(BaseModel):
    display_name: str = Field(default="Local User", min_length=1, max_length=100)
    data_dir: str = Field(default="~/Library/Application Support/DesktopMate", max_length=500)


class ModelSettings(BaseModel):
    provider: str = Field(default="openai", min_length=1, max_length=100)
    model: str = Field(default="gpt-4.1-mini", min_length=1, max_length=100)
    temperature: float = Field(default=0.4, ge=0.0, le=2.0)
    context_length: int = Field(default=128_000, ge=1_000, le=2_000_000)


class ApiKeyRecord(BaseModel):
    provider: str
    has_key: bool
    masked: str | None = None
    updated_at: str | None = None
    verified_at: str | None = None


class ConfiguredModel(BaseModel):
    provider: str = Field(min_length=1, max_length=100)
    model: str = Field(min_length=1, max_length=100)
    verified_at: str


class SettingsStore:
    def __init__(self, settings_dir: Path) -> None:
        self.settings_dir = settings_dir
        self.settings_dir.mkdir(parents=True, exist_ok=True)
        self.settings_path = settings_dir / "settings.json"
        self.keys_path = settings_dir / "keys.enc"
        self.key_path = settings_dir / "key"
        # D3: keys 文件 mtime 缓存——文件未变化时避免每次 Fernet 解密读盘
        self._keys_cache: dict[str, object] | None = None
        self._keys_cache_mtime: float | None = None
        self._fernet = self._load_or_create_fernet()

    # --- 通用读写 ---

    def _read_json(self) -> dict[str, object]:
        if not self.settings_path.exists():
            return {}
        try:
            raw = json.loads(self.settings_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
        return raw if isinstance(raw, dict) else {}

    def _write_json(self, data: dict[str, object]) -> None:
        self.settings_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    # --- Profile ---

    def get_profile(self) -> ProfileSettings:
        raw = self._read_json().get("profile", {})
        if isinstance(raw, dict):
            return ProfileSettings(**{key: value for key, value in raw.items() if key in ProfileSettings.model_fields})
        return ProfileSettings()

    def set_profile(self, profile: ProfileSettings) -> ProfileSettings:
        data = self._read_json()
        data["profile"] = profile.model_dump()
        self._write_json(data)
        return profile

    # --- Models ---

    def get_models(self) -> ModelSettings:
        raw = self._read_json().get("models", {})
        if isinstance(raw, dict):
            return ModelSettings(**{key: value for key, value in raw.items() if key in ModelSettings.model_fields})
        return ModelSettings()

    def has_models(self) -> bool:
        """settings.json 中是否存在用户显式保存过的 models 配置。

        ``get_models()`` 在缺失时会返回默认 ``ModelSettings``，调用方需要
        用本方法区分"用户保存过"与"从未保存"，避免把默认值当作显式选择。
        """
        raw = self._read_json().get("models")
        return isinstance(raw, dict) and bool(raw)

    def set_models(self, models: ModelSettings) -> ModelSettings:
        data = self._read_json()
        data["models"] = models.model_dump()
        self._write_json(data)
        return models

    def list_configured_models(self) -> list[ConfiguredModel]:
        raw = self._read_json().get("configured_models", [])
        if not isinstance(raw, list):
            return []
        configured: list[ConfiguredModel] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            try:
                configured.append(ConfiguredModel.model_validate(item))
            except ValueError:
                continue
        return configured

    def has_configured_model(self, provider: str, model: str) -> bool:
        return any(
            item.provider == provider and item.model == model
            for item in self.list_configured_models()
        )

    def add_configured_model(self, provider: str, model: str) -> ConfiguredModel:
        configured = [
            item
            for item in self.list_configured_models()
            if not (item.provider == provider and item.model == model)
        ]
        entry = ConfiguredModel(
            provider=provider,
            model=model,
            verified_at=datetime.now(UTC).isoformat(),
        )
        configured.append(entry)
        data = self._read_json()
        data["configured_models"] = [item.model_dump() for item in configured]
        self._write_json(data)
        return entry

    def remove_configured_model(self, provider: str, model: str) -> bool:
        configured = self.list_configured_models()
        retained = [
            item for item in configured if not (item.provider == provider and item.model == model)
        ]
        if len(retained) == len(configured):
            return False
        data = self._read_json()
        data["configured_models"] = [item.model_dump() for item in retained]
        self._write_json(data)
        return True

    # --- Skills ---

    def get_skill_enabled(self, name: str) -> bool | None:
        raw = self._read_json().get("skills", {})
        if not isinstance(raw, dict):
            return None
        value = raw.get(name)
        return value if isinstance(value, bool) else None

    def set_skill_enabled(self, name: str, enabled: bool) -> None:
        data = self._read_json()
        skills = data.get("skills", {})
        if not isinstance(skills, dict):
            skills = {}
        skills[name] = enabled
        data["skills"] = skills
        self._write_json(data)

    # --- API Keys（加密存储） ---

    def list_api_keys(self) -> list[ApiKeyRecord]:
        stored = self._read_keys()
        return [
            ApiKeyRecord(
                provider=provider,
                has_key=True,
                masked=_mask_key(str(item.get("api_key", ""))),
                updated_at=str(item.get("updated_at", "")),
                verified_at=str(item.get("verified_at", "")) or None,
            )
            for provider, item in stored.items()
            if isinstance(item, dict) and item.get("api_key")
        ]

    def save_api_key(self, provider: str, api_key: str) -> ApiKeyRecord:
        stored = self._read_keys()
        stored[provider] = {
            "api_key": api_key,
            "updated_at": datetime.now(UTC).isoformat(),
        }
        self._write_keys(stored)
        return ApiKeyRecord(
            provider=provider,
            has_key=True,
            masked=_mask_key(api_key),
            updated_at=stored[provider]["updated_at"],
            verified_at=None,
        )

    def mark_api_key_verified(self, provider: str) -> ApiKeyRecord | None:
        stored = self._read_keys()
        item = stored.get(provider)
        if not isinstance(item, dict) or not item.get("api_key"):
            return None
        verified_at = datetime.now(UTC).isoformat()
        item["verified_at"] = verified_at
        stored[provider] = item
        self._write_keys(stored)
        return ApiKeyRecord(
            provider=provider,
            has_key=True,
            masked=_mask_key(str(item["api_key"])),
            updated_at=str(item.get("updated_at", "")) or None,
            verified_at=verified_at,
        )

    def delete_api_key(self, provider: str) -> bool:
        stored = self._read_keys()
        if provider not in stored:
            return False
        del stored[provider]
        self._write_keys(stored)
        return True

    def get_api_key(self, provider: str) -> str | None:
        stored = self._read_keys()
        item = stored.get(provider)
        if not isinstance(item, dict):
            return None
        value = item.get("api_key")
        return str(value) if value else None

    def _read_keys(self) -> dict[str, object]:
        if not self.keys_path.exists():
            self._keys_cache = None
            self._keys_cache_mtime = None
            return {}
        # mtime 未变 → 返回缓存，避免每次读盘 + Fernet 解密（chat 高频路径）
        try:
            mtime = self.keys_path.stat().st_mtime
        except OSError:
            return self._keys_cache or {}
        if self._keys_cache is not None and mtime == self._keys_cache_mtime:
            return self._keys_cache
        try:
            token = self._fernet.decrypt(self.keys_path.read_bytes())
            data = json.loads(token.decode("utf-8"))
        except (InvalidToken, json.JSONDecodeError, OSError):
            return {}
        cached = data if isinstance(data, dict) else {}
        self._keys_cache = cached
        self._keys_cache_mtime = mtime
        return cached

    def _write_keys(self, data: dict[str, object]) -> None:
        payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.keys_path.write_bytes(self._fernet.encrypt(payload))
        os.chmod(self.keys_path, 0o600)

    def _load_or_create_fernet(self) -> Fernet:
        if self.key_path.exists():
            key = self.key_path.read_bytes().strip()
        else:
            key = Fernet.generate_key()
            self.key_path.write_bytes(key)
            os.chmod(self.key_path, 0o600)
        return Fernet(key)


def _mask_key(api_key: str) -> str:
    if len(api_key) <= 8:
        return f"{'*' * max(0, len(api_key) - 4)}{api_key[-4:]}"
    return f"{api_key[:3]}...{api_key[-4:]}"
