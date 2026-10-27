"""Skills API（S4）：内置工具 + 导入的 Skill Package 统一视图。

- ``GET /api/skills``：内置工具与已安装包的合并列表。
- ``GET /api/skills/{name}``：详情（含安装路径、权限列表）。
- ``POST /api/skills/import``：导入 zip（防 Zip Slip，默认 disabled）。
- ``PATCH /api/skills/{name}``：启用/禁用。
- ``DELETE /api/skills/{name}``：删除已安装包（内置工具不可删）。
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from pydantic import BaseModel

from desktopmate_agent.api.errors import ApiError
from desktopmate_agent.api.routes import require_auth
from desktopmate_agent.settings.store import SettingsStore
from desktopmate_agent.skills.store import SkillPackage, SkillPackageError, SkillPackageStore
from desktopmate_agent.tools.registry import ToolRegistry

MAX_IMPORT_BYTES = 6 * 1024 * 1024  # 6MB（含 zip 上限 5MB + 余量）


class SkillUpdate(BaseModel):
    enabled: bool


def create_skills_router(
    package_store: SkillPackageStore,
    settings_store: SettingsStore,
    tool_registry: ToolRegistry,
) -> APIRouter:
    router = APIRouter(dependencies=[Depends(require_auth)])

    def _effective_enabled(name: str) -> bool:
        stored = settings_store.get_skill_enabled(name)
        if stored is not None:
            return stored
        if name in _registered_tool_names():
            return tool_registry.is_enabled(name)
        return False

    def _registered_tool_names() -> set[str]:
        return {tool.name for tool in tool_registry.list()}

    def _package_item(package: SkillPackage) -> dict[str, object]:
        return {
            "name": package.name,
            "description": package.description,
            "risk_level": package.risk_level,
            "requires_permission": package.requires_permission,
            "enabled": _effective_enabled(package.name),
            "version": package.version,
            "type": package.type,
            "prompt": package.effective_prompt,
            "builtin": False,
        }

    def _tool_item(name: str) -> dict[str, object]:
        # 用 list() 查找（get() 对禁用工具抛 KeyError）
        tool = next((item for item in tool_registry.list() if item.name == name), None)
        if tool is None:
            raise HTTPException(
                status_code=404,
                detail={"code": "skill_not_found", "message": f"技能 {name} 不存在"},
            )
        return {
            "name": tool.name,
            "description": tool.description,
            "risk_level": str(tool.risk_level),
            "requires_permission": tool.requires_permission,
            "enabled": _effective_enabled(tool.name),
            "builtin": True,
        }

    def _sync_tool_skills() -> None:
        """热同步：扫描 skills 目录中新增的 tool 技能并注册为工具。

        支持"复制文件夹即用"——用户把 skill 文件夹放进 skills 目录后，
        无需重启服务，调用列表接口即完成注册（默认禁用，沿用历史启停状态）。
        """
        registered = _registered_tool_names()
        for package in package_store.installed():
            if package.type != "tool":
                continue
            if package.name in registered:
                continue
            try:
                from desktopmate_agent.skills.loader import load_tool_skill

                tool = load_tool_skill(package, Path(package.install_path))
                tool_registry.register(tool)
                tool_registry.set_enabled(tool.name, False)
                stored = settings_store.get_skill_enabled(tool.name)
                if stored is not None:
                    tool_registry.set_enabled(tool.name, stored)
            except Exception:
                # 单个技能加载失败不影响其他技能，用户可看到报错后修复
                continue

    @router.get("/api/skills")
    def list_skills(_: None = Depends(require_auth)) -> dict[str, object]:
        # 热同步：复制文件夹进来的新 tool 技能在此注册（无需重启）
        _sync_tool_skills()
        # 合并：内置工具 + 已安装包；同名时包优先（覆盖内置）
        items: dict[str, dict[str, object]] = {
            tool.name: _tool_item(tool.name) for tool in tool_registry.list()
        }
        for package in package_store.installed():
            items[package.name] = _package_item(package)
        return {"skills": list(items.values())}

    @router.get("/api/skills/{name}")
    def get_skill(name: str, _: None = Depends(require_auth)) -> dict[str, object]:
        package = package_store.get(name)
        if package is not None:
            return {
                **_package_item(package),
                "version": package.version,
                "entry": package.entry,
                "permissions": package.permissions,
                "install_path": package.install_path,
            }
        if tool_registry.has(name):
            return _tool_item(name)
        raise HTTPException(
            status_code=404,
            detail={"code": "skill_not_found", "message": f"技能 {name} 不存在"},
        )

    @router.post("/api/skills/import")
    async def import_skill(
        file: UploadFile,
        _: None = Depends(require_auth),
    ) -> dict[str, object]:
        if not (file.filename or "").lower().endswith(".zip"):
            raise ApiError(code="unsupported_format", message="仅支持 .zip 格式的 Skill Package")
        content = await file.read()
        if len(content) > MAX_IMPORT_BYTES:
            raise ApiError(code="package_too_large", message="Skill Package 过大", status_code=413)
        try:
            package = package_store.install_zip(content)
        except SkillPackageError as exc:
            raise ApiError(code=exc.code, message=str(exc), status_code=400) from exc
        settings_store.set_skill_enabled(package.name, False)  # 默认 disabled

        # Tool 形态：加载入口代码并把工具注册进 registry（默认禁用，启用后生效）
        if package.type == "tool":
            try:
                from desktopmate_agent.skills.loader import load_tool_skill

                installed = package_store.get(package.name)
                if installed is None:
                    raise SkillPackageError("技能安装目录丢失", code="install_missing")
                tool = load_tool_skill(package, Path(installed.install_path))
                if tool_registry.has(tool.name):
                    raise SkillPackageError(
                        f"工具 {tool.name} 已存在（内置或已导入的技能重名）",
                        code="tool_name_conflict",
                    )
                tool_registry.register(tool)
                tool_registry.set_enabled(tool.name, False)
            except SkillPackageError as exc:
                package_store.delete(package.name)
                raise ApiError(code=exc.code, message=str(exc), status_code=400) from exc

        return {
            "skill": {
                "name": package.name,
                "version": package.version,
                "enabled": False,
                "risk_level": package.risk_level,
                "requires_permission": package.requires_permission,
            }
        }

    @router.patch("/api/skills/{name}")
    def update_skill(
        name: str,
        payload: SkillUpdate,
        _: None = Depends(require_auth),
    ) -> dict[str, object]:
        package = package_store.get(name)
        if package is not None:
            settings_store.set_skill_enabled(name, payload.enabled)
            return _package_item(package)
        if name in _registered_tool_names():
            tool_registry.set_enabled(name, payload.enabled)
            settings_store.set_skill_enabled(name, payload.enabled)
            return _tool_item(name)
        raise HTTPException(
            status_code=404,
            detail={"code": "skill_not_found", "message": f"技能 {name} 不存在"},
        )

    @router.delete("/api/skills/{name}")
    def delete_skill(name: str, _: None = Depends(require_auth)) -> dict[str, bool]:
        if name in _registered_tool_names() and package_store.get(name) is None:
            raise ApiError(
                code="builtin_skill",
                message="内置技能不可删除",
                status_code=422,
            )
        package = package_store.get(name)
        if package is not None and package.type == "tool":
            # Tool 技能删除：同步从注册表反注册（先禁用再移除，避免 get 抛错）
            if name in _registered_tool_names():
                tool_registry.set_enabled(name, False)
                tool_registry.unregister(name)
        if not package_store.delete(name):
            raise HTTPException(
                status_code=404,
                detail=dict(code="skill_not_found", message=f"技能 {name} 不存在"),
            )
        settings_store.set_skill_enabled(name, False)
        return {"deleted": True}

    return router
