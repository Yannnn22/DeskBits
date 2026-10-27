"""Tool Skill（可执行技能）加载与生命周期测试。"""

from __future__ import annotations

import asyncio
import io
import json
import zipfile
from pathlib import Path

import pytest

from desktopmate_agent.skills.loader import load_tool_skill
from desktopmate_agent.skills.store import SkillPackageError, SkillPackageStore
from desktopmate_agent.tools.base import BaseTool
from desktopmate_agent.tools.registry import ToolRegistry

PALINDROME_ENTRY = '''
"""回文检查工具（测试用）"""
from desktopmate_agent.tools.base import BaseTool, ToolResult
from pydantic import BaseModel as SchemaModel, Field

class PalindromeArgs(SchemaModel):
    text: str = Field(description="待检查的文本")

class palindrome_check(BaseTool):
    name = "palindrome_check"
    description = "检查一段文本是否是回文"
    args_schema = PalindromeArgs

    async def run(self, args: PalindromeArgs) -> ToolResult:
        s = args.text.replace(" ", "")
        ok = s == s[::-1]
        return ToolResult(status="success", summary="是回文" if ok else "不是回文", data={"palindrome": ok})
'''

TOOL_MANIFEST = {
    "name": "palindrome_check",
    "version": "0.1.0",
    "description": "回文检查工具技能",
    "type": "tool",
    "entry": "tools/entry.py",
    "risk_level": "low",
}


def make_zip(name: str, manifest: dict, entry: str | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(f"{name}/skill.json", json.dumps(manifest, ensure_ascii=False))
        if entry is not None:
            zf.writestr(f"{name}/tools/entry.py", entry)
    buf.seek(0)
    return buf.read()


def test_tool_skill_install_load_and_run(tmp_path: Path) -> None:
    store = SkillPackageStore(tmp_path / "skills")
    package = store.install_zip(make_zip("palindrome_check", TOOL_MANIFEST, PALINDROME_ENTRY))
    assert package.type == "tool"

    tool = load_tool_skill(package, Path(package.install_path))
    assert isinstance(tool, BaseTool)
    assert tool.name == "palindrome_check"

    result = asyncio.run(tool.run(tool.args_schema(text="上海自来水来自海上")))
    assert result.status == "success"
    assert "是回文" in result.summary


def test_tool_skill_registry_lifecycle(tmp_path: Path) -> None:
    store = SkillPackageStore(tmp_path / "skills")
    package = store.install_zip(make_zip("palindrome_check", TOOL_MANIFEST, PALINDROME_ENTRY))
    tool = load_tool_skill(package, Path(package.install_path))

    registry = ToolRegistry()
    registry.register(tool)
    registry.set_enabled("palindrome_check", False)
    assert not registry.is_enabled("palindrome_check")

    registry.set_enabled("palindrome_check", True)
    assert registry.is_enabled("palindrome_check")

    registry.unregister("palindrome_check")
    assert not registry.has("palindrome_check")


def test_tool_skill_missing_entry_rejected_at_install(tmp_path: Path) -> None:
    store = SkillPackageStore(tmp_path / "skills")
    bad = dict(TOOL_MANIFEST, name="bad", entry="tools/missing.py")
    with pytest.raises(SkillPackageError, match="入口文件不存在"):
        store.install_zip(make_zip("bad", bad))


def test_tool_skill_non_py_entry_rejected(tmp_path: Path) -> None:
    store = SkillPackageStore(tmp_path / "skills")
    bad = dict(TOOL_MANIFEST, name="badext", entry="tools/entry.txt")
    with pytest.raises(SkillPackageError, match=".py"):
        store.install_zip(make_zip("badext", bad, "x = 1\n"))


def test_tool_skill_no_basetool_subclass_rejected(tmp_path: Path) -> None:
    store = SkillPackageStore(tmp_path / "skills")
    package = store.install_zip(make_zip("noop", dict(TOOL_MANIFEST, name="noop"), "x = 1\n"))
    with pytest.raises(SkillPackageError, match="未定义任何 BaseTool"):
        load_tool_skill(package, Path(package.install_path))


def test_tool_skill_name_mismatch_rejected(tmp_path: Path) -> None:
    store = SkillPackageStore(tmp_path / "skills")
    mismatch = PALINDROME_ENTRY.replace('name = "palindrome_check"', 'name = "other_name"')
    package = store.install_zip(make_zip("mismatch", dict(TOOL_MANIFEST, name="mismatch"), mismatch))
    with pytest.raises(SkillPackageError, match="不一致"):
        load_tool_skill(package, Path(package.install_path))


def test_tool_skill_api_lifecycle(tmp_path: Path) -> None:
    """HTTP API 全生命周期：导入 → 列表 → 启用 → 删除（反注册）。"""
    from fastapi.testclient import TestClient

    from desktopmate_agent.config import Settings
    from desktopmate_agent.main import create_app

    settings = Settings(
        trace_dir=tmp_path / "traces",
        settings_dir=tmp_path / "settings",
        app_data_dir=tmp_path,
        uploads_dir=tmp_path / "uploads",
        auth_token="t",
        openai_api_key=None,
        deepseek_api_key=None,
    )
    headers = {"X-DesktopMate-Token": "t"}
    client = TestClient(create_app(settings=settings))

    manifest = dict(TOOL_MANIFEST, name="echo_tool")
    entry = PALINDROME_ENTRY.replace("palindrome_check", "echo_tool")
    zip_bytes = make_zip("echo_tool", manifest, entry)

    # 导入（默认禁用）
    r = client.post(
        "/api/skills/import",
        headers=headers,
        files={"file": ("echo_tool.zip", zip_bytes, "application/zip")},
    )
    assert r.status_code == 200
    assert r.json()["skill"]["name"] == "echo_tool"
    assert r.json()["skill"]["enabled"] is False

    def find(name: str):
        return next(
            (s for s in client.get("/api/skills", headers=headers).json()["skills"] if s["name"] == name),
            None,
        )

    # 列表：默认禁用 + risk 透传
    item = find("echo_tool")
    assert item is not None
    assert item["enabled"] is False
    assert item["type"] == "tool"

    # 启用生效
    assert client.patch("/api/skills/echo_tool", headers=headers, json={"enabled": True}).status_code == 200
    assert find("echo_tool")["enabled"] is True

    # 删除 → 反注册
    assert client.delete("/api/skills/echo_tool", headers=headers).status_code == 200
    assert find("echo_tool") is None

    # 非法包（entry 缺失）：导入拒绝且不残留
    bad = dict(TOOL_MANIFEST, name="bad_tool", entry="tools/missing.py")
    r_bad = client.post(
        "/api/skills/import",
        headers=headers,
        files={"file": ("bad_tool.zip", make_zip("bad_tool", bad), "application/zip")},
    )
    assert r_bad.status_code == 400
    assert find("bad_tool") is None


def test_copy_folder_to_skills_dir_hot_sync(tmp_path: Path) -> None:
    """复制 skill 文件夹到 skills 目录 = 导入（无需重启，调用列表即热同步）。"""
    from fastapi.testclient import TestClient

    from desktopmate_agent.config import Settings
    from desktopmate_agent.main import create_app

    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    settings = Settings(
        trace_dir=tmp_path / "traces",
        settings_dir=tmp_path / "settings",
        app_data_dir=tmp_path,
        uploads_dir=tmp_path / "uploads",
        auth_token="t",
        openai_api_key=None,
        deepseek_api_key=None,
        skills_dir=skills_dir,
    )
    headers = {"X-DesktopMate-Token": "t"}
    client = TestClient(create_app(settings=settings))

    # 启动时目录为空
    assert not any(s.get("type") == "tool" for s in client.get("/api/skills", headers=headers).json()["skills"])

    # 运行中复制文件夹（不重启）
    tool_dir = skills_dir / "my_calc"
    (tool_dir / "tools").mkdir(parents=True)
    (tool_dir / "skill.json").write_text(
        json.dumps(
            {
                "name": "my_calc",
                "version": "1.0.0",
                "description": "计算器",
                "type": "tool",
                "entry": "tools/entry.py",
                "risk_level": "low",
            }
        ),
        encoding="utf-8",
    )
    (tool_dir / "tools" / "entry.py").write_text(PALINDROME_ENTRY.replace("palindrome_check", "my_calc"), encoding="utf-8")

    # 调用列表 → 热同步注册
    skills = client.get("/api/skills", headers=headers).json()["skills"]
    item = next((s for s in skills if s.get("name") == "my_calc"), None)
    assert item is not None
    assert item["type"] == "tool"
    assert item["enabled"] is False

    # 可启用
    assert client.patch("/api/skills/my_calc", headers=headers, json={"enabled": True}).status_code == 200
