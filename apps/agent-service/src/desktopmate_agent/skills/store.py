"""Skill Package 安装存储（S4）。

Skill Package = 一个目录（zip 导入），结构：

    skill-name/
      skill.json      # 元数据（name/version/description/entry/permissions/risk_level）
      README.md
      prompts/        # 预留
      tools/          # 预留

安全约束（计划 S4）：
- zip 解压防 Zip Slip（绝对路径 / ../ 一律拒绝）。
- 限制总大小与文件数量。
- skill.json schema 校验失败拒绝导入。
- 导入后默认 disabled，由 SettingsStore 记录启用状态。
"""

from __future__ import annotations

import io
import json
import re
import shutil
import tempfile
import zipfile
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

MAX_ZIP_BYTES = 5 * 1024 * 1024  # 5MB
MAX_ENTRIES = 200
MAX_EXTRACTED_BYTES = 20 * 1024 * 1024  # 20MB 解压上限
NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
VALID_RISK_LEVELS = {"low", "medium", "high"}


class SkillPackageError(Exception):
    """可展示给用户的导入错误。"""

    def __init__(self, message: str, *, code: str = "invalid_skill_package") -> None:
        super().__init__(message)
        self.code = code


class SkillPackage(BaseModel):
    name: str
    version: str
    description: str = ""
    # skill 形态：prompt = 注入系统提示词的指令；tool = 可执行工具（entry 指向代码）。
    # 预留的 prompts/ tools/ 目录字段保持向后兼容（旧包无 type 默认按 prompt 处理）。
    type: str = "prompt"
    entry: str = "README.md"
    # prompt skill 的核心内容：一段注入系统提示词的指令文本。
    # 也支持把内容放到包内 prompts/main.md，manifest 的 prompt 字段优先。
    prompt: str = ""
    permissions: list[str] = Field(default_factory=list)
    risk_level: str = "medium"
    install_path: str = ""

    @property
    def requires_permission(self) -> bool:
        return self.risk_level != "low"

    @property
    def effective_prompt(self) -> str:
        """返回注入用的提示词：manifest.prompt 优先，否则读包内 prompts/main.md。"""
        if self.prompt.strip():
            return self.prompt.strip()
        if self.install_path:
            candidate = Path(self.install_path) / "prompts" / "main.md"
            try:
                if candidate.exists():
                    return candidate.read_text(encoding="utf-8").strip()
            except OSError:
                pass
        return ""


class SkillPackageStore:
    def __init__(self, skills_dir: Path) -> None:
        self.skills_dir = skills_dir
        self.skills_dir.mkdir(parents=True, exist_ok=True)

    # --- 查询 ---

    def installed(self) -> list[SkillPackage]:
        packages: list[SkillPackage] = []
        for package_dir in sorted(self.skills_dir.iterdir()):
            if not package_dir.is_dir():
                continue
            package = self._load_from_dir(package_dir)
            if package is not None:
                packages.append(package)
        return packages

    def active_prompt_skills(self, enabled_names: set[str]) -> list[SkillPackage]:
        """返回已启用、且是 prompt 形态、含有效提示词的技能包。

        enabled_names 由调用方从 SettingsStore 读取（skills 目录只管磁盘，
        不管启停状态）。排序保证注入顺序稳定。
        """
        result: list[SkillPackage] = []
        for package in self.installed():
            if package.name not in enabled_names:
                continue
            if package.type != "prompt":
                continue
            if not package.effective_prompt:
                continue
            result.append(package)
        result.sort(key=lambda package: package.name)
        return result

    def get(self, name: str) -> SkillPackage | None:
        package_dir = self.skills_dir / name
        if not package_dir.is_dir():
            return None
        return self._load_from_dir(package_dir)

    def _load_from_dir(self, package_dir: Path) -> SkillPackage | None:
        manifest = package_dir / "skill.json"
        if not manifest.exists():
            return None
        try:
            raw = json.loads(manifest.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        try:
            package = SkillPackage(**raw)
        except ValidationError:
            return None
        return package.model_copy(update={"install_path": str(package_dir)})

    # --- 安装 ---

    def install_zip(self, content: bytes) -> SkillPackage:
        if len(content) > MAX_ZIP_BYTES:
            raise SkillPackageError(
                f"Skill Package 超过大小上限 {MAX_ZIP_BYTES // 1024 // 1024}MB",
                code="package_too_large",
            )
        staging = Path(tempfile.mkdtemp(prefix=".skill_staging_", dir=self.skills_dir))
        try:
            self._extract_safely(content, staging)
            manifest = self._find_manifest(staging)
            if manifest is None:
                raise SkillPackageError("未找到 skill.json，包结构无效")
            package = self._validate_manifest(manifest)
            # tool 形态：entry 指向的 .py 必须在包内（尽早失败，不等 load 时才报）
            if package.type == "tool":
                entry_path = (manifest.parent / package.entry).resolve()
                if not entry_path.is_relative_to(manifest.parent.resolve()):
                    raise SkillPackageError(
                        f"tool 技能 {package.name} 的 entry 超出包目录范围",
                        code="entry_outside_package",
                    )
                if not entry_path.exists():
                    raise SkillPackageError(
                        f"tool 技能 {package.name} 的入口文件不存在: {package.entry}",
                        code="entry_not_found",
                    )
            target = self.skills_dir / package.name
            if target.exists():
                raise SkillPackageError(
                    f"技能 {package.name} 已安装，请先删除再导入",
                    code="skill_already_installed",
                )
            shutil.move(str(manifest.parent), str(target))
            return package.model_copy(update={"install_path": str(target)})
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    def _extract_safely(self, content: bytes, staging: Path) -> None:
        try:
            archive = zipfile.ZipFile(io.BytesIO(content))
        except zipfile.BadZipFile as exc:
            raise SkillPackageError("不是有效的 zip 文件") from exc
        names = archive.namelist()
        if len(names) > MAX_ENTRIES:
            raise SkillPackageError(f"文件数量超过上限 {MAX_ENTRIES}", code="too_many_entries")
        total_size = 0
        for info in archive.infolist():
            total_size += info.file_size
            if total_size > MAX_EXTRACTED_BYTES:
                raise SkillPackageError("解压后总大小超过上限 20MB", code="package_too_large")
            normalized = info.filename.replace("\\", "/")
            if normalized.startswith("/") or ".." in normalized.split("/"):
                raise SkillPackageError(
                    f"非法路径: {info.filename!r}（不允许绝对路径或 ..）",
                    code="unsafe_path",
                )
        for info in archive.infolist():
            normalized = info.filename.replace("\\", "/")
            if normalized.startswith("__MACOSX/") or normalized.endswith(".DS_Store"):
                continue
            target = (staging / normalized).resolve()
            if not target.is_relative_to(staging.resolve()):
                raise SkillPackageError(
                    f"非法路径: {info.filename!r}",
                    code="unsafe_path",
                )
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, target.open("wb") as dest:
                shutil.copyfileobj(source, dest)

    @staticmethod
    def _find_manifest(staging: Path) -> Path | None:
        direct = staging / "skill.json"
        if direct.exists():
            return direct
        children = [p for p in staging.iterdir() if p.is_dir()]
        if len(children) == 1:
            nested = children[0] / "skill.json"
            if nested.exists():
                return nested
        return None

    @staticmethod
    def _validate_manifest(manifest: Path) -> SkillPackage:
        try:
            raw = json.loads(manifest.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            raise SkillPackageError("skill.json 不是合法 JSON") from exc
        if not isinstance(raw, dict):
            raise SkillPackageError("skill.json 必须是 JSON 对象")
        try:
            package = SkillPackage(**raw)
        except ValidationError as exc:
            raise SkillPackageError(f"skill.json 校验失败: {exc}") from exc
        if not NAME_PATTERN.match(package.name):
            raise SkillPackageError(
                f"技能名 {package.name!r} 非法（须匹配 {NAME_PATTERN.pattern}）"
            )
        if package.risk_level not in VALID_RISK_LEVELS:
            raise SkillPackageError(f"risk_level 必须是 low|medium|high，收到 {package.risk_level!r}")
        if package.type not in {"prompt", "tool"}:
            raise SkillPackageError(
                f"type 必须是 prompt|tool，收到 {package.type!r}",
                code="invalid_skill_type",
            )
        if package.type == "prompt" and not package.effective_prompt:
            raise SkillPackageError(
                "prompt skill 需要提供 prompt 字段或 prompts/main.md 文件",
                code="missing_prompt",
            )
        if package.type == "tool" and (
            not package.entry or not package.entry.endswith(".py")
        ):
            raise SkillPackageError(
                "tool skill 必须声明 entry 指向包内的 .py 文件（如 tools/entry.py）",
                code="missing_tool_entry",
            )
        return package

    # --- 删除 ---

    def delete(self, name: str) -> bool:
        target = self.skills_dir / name
        if not target.is_dir() or not (target / "skill.json").exists():
            return False
        shutil.rmtree(target)
        return True
