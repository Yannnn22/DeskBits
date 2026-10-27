"""Tool Skill 加载器：把用户导入的可执行工具技能加载为运行时工具。

Tool Skill 包结构（zip 解压后）：
    skill-name/
      skill.json          type="tool"，entry 指向 tools/entry.py
      tools/entry.py      定义 BaseTool 子类（name/description/args_schema/run）

加载规则：
- entry.py 内定义的所有 BaseTool 直接子类都会被加载（可定义多个工具）
- 工具的 name 必须与 skill 包名一致（技能 = 单个工具，避免命名混乱）
- 任何导入/校验失败都抛 SkillPackageError（可展示给用户），绝不静默半加载
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from desktopmate_agent.skills.store import SkillPackage, SkillPackageError
from desktopmate_agent.tools.base import BaseTool


def load_tool_skill(package: SkillPackage, package_dir: Path) -> BaseTool:
    """加载一个 tool 形态技能，返回其 BaseTool 实例。

    参数：
        package: 已安装的技能包元数据（type 必须为 "tool"）
        package_dir: 技能包解压目录

    返回：
        实例化的 BaseTool（技能包中定义的工具）

    异常：
        SkillPackageError: 包结构/代码不符合要求时抛出（message 可直接展示给用户）
    """
    if package.type != "tool":
        raise SkillPackageError(
            f"技能 {package.name} 不是 tool 形态（type={package.type}）",
            code="not_a_tool_skill",
        )

    entry_rel = package.entry
    # entry 默认值 README.md 对 tool 技能无意义，强制要求显式 entry
    if not entry_rel or entry_rel == "README.md" or not entry_rel.endswith(".py"):
        raise SkillPackageError(
            f"tool 技能 {package.name} 必须声明 entry（指向包内的 .py 文件，如 tools/entry.py）",
            code="missing_tool_entry",
        )
    entry_path = (package_dir / entry_rel).resolve()
    # 防止 entry 逃逸出包目录（如 entry="../evil.py"）
    if not entry_path.is_relative_to(package_dir.resolve()):
        raise SkillPackageError(
            f"tool 技能 {package.name} 的 entry 超出包目录范围",
            code="entry_outside_package",
        )
    if not entry_path.exists():
        raise SkillPackageError(
            f"tool 技能 {package.name} 的入口文件不存在: {entry_rel}",
            code="entry_not_found",
        )

    module_name = f"_deskbits_skill_{package.name}"
    try:
        spec = importlib.util.spec_from_file_location(module_name, entry_path)
        if spec is None or spec.loader is None:
            raise SkillPackageError(
                f"无法加载技能 {package.name} 的入口模块", code="entry_load_failed"
            )
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
    except SkillPackageError:
        raise
    except Exception as exc:  # 导入期任意异常都转成用户可读错误
        raise SkillPackageError(
            f"技能 {package.name} 代码加载失败: {exc}", code="entry_import_failed"
        ) from exc

    # 收集模块内定义的 BaseTool 直接子类
    tool_classes: list[type[BaseTool]] = []
    for attr_name in dir(module):
        attr = getattr(module, attr_name)
        if (
            isinstance(attr, type)
            and issubclass(attr, BaseTool)
            and attr is not BaseTool
        ):
            # 只收集模块内直接定义的类（排除 import 进来的）
            if attr.__module__ == module_name:
                tool_classes.append(attr)

    if not tool_classes:
        raise SkillPackageError(
            f"技能 {package.name} 的入口文件未定义任何 BaseTool 子类",
            code="no_tool_defined",
        )

    # 实例化：允许无参构造；有参构造则用默认值（风险由 manifest risk_level 承担）
    tool: BaseTool | None = None
    last_error: str | None = None
    for cls in tool_classes:
        try:
            candidate = cls()
            if candidate.name != package.name:
                last_error = (
                    f"工具名 {candidate.name!r} 与技能包名 {package.name!r} 不一致"
                )
                continue
            tool = candidate
            break
        except TypeError as exc:
            last_error = f"工具 {cls.__name__} 无法实例化（需要参数？）: {exc}"
        except Exception as exc:
            last_error = f"工具 {cls.__name__} 实例化失败: {exc}"

    if tool is None:
        raise SkillPackageError(
            last_error or f"技能 {package.name} 没有可加载的工具",
            code="tool_instantiation_failed",
        )

    # 风险属性统一以 manifest 为准（用户代码里写了也不信任，防绕过权限门）。
    # manifest 的 low/medium/high 映射到 RiskLevel：low→READ（免确认），
    # medium/high→EXTERNAL（弹权限确认）。
    try:
        from desktopmate_agent.tools.base import RiskLevel

        mapped = RiskLevel.READ if package.risk_level == "low" else RiskLevel.EXTERNAL
        tool.__class__.risk_level = mapped
        tool.__class__.requires_permission = package.requires_permission
    except Exception as exc:
        raise SkillPackageError(
            f"技能 {package.name} 风险属性配置失败: {exc}",
            code="risk_config_failed",
        ) from exc
    return tool
