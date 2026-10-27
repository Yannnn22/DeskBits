from __future__ import annotations

from desktopmate_agent.tools.base import BaseTool


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, BaseTool] = {}
        self._disabled: set[str] = set()

    def register(self, tool: BaseTool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Tool '{tool.name}' is already registered")
        self._tools[tool.name] = tool

    def get(self, name: str) -> BaseTool:
        try:
            tool = self._tools[name]
        except KeyError as exc:
            raise KeyError(f"Tool '{name}' is not registered") from exc
        if name in self._disabled:
            raise KeyError(f"Tool '{name}' is disabled")
        return tool

    def has(self, name: str) -> bool:
        return name in self._tools and name not in self._disabled

    def safe_get(self, name: str) -> BaseTool | None:
        """返回工具或 None（未注册/已禁用），供执行后审计等非关键路径使用。"""
        if not self.has(name):
            return None
        return self._tools[name]

    def list(self) -> list[BaseTool]:
        return list(self._tools.values())

    def is_enabled(self, name: str) -> bool:
        return name in self._tools and name not in self._disabled

    def set_enabled(self, name: str, enabled: bool) -> None:
        if name not in self._tools:
            raise KeyError(f"Tool '{name}' is not registered")
        if enabled:
            self._disabled.discard(name)
        else:
            self._disabled.add(name)

    def unregister(self, name: str) -> None:
        """移除工具（用于用户删除导入的 Tool Skill）。"""
        if name not in self._tools:
            raise KeyError(f"Tool '{name}' is not registered")
        self._tools.pop(name)
        self._disabled.discard(name)

    def list_for_mode(self, mode: str) -> list[BaseTool]:
        return [
            tool
            for tool in self.list()
            if tool.agent_visible and self.is_enabled(tool.name)
        ]

    def tool_prompt(self, mode: str) -> str:
        """Build the router contract from the registered Pydantic schemas."""
        parts: list[str] = []
        for tool in self.list_for_mode(mode):
            schema = tool.args_schema.model_json_schema()
            properties = schema.get("properties", {})
            required = set(schema.get("required", []))
            arguments = ", ".join(
                f"{name}{'' if name in required else '?'}"
                for name in properties
            )
            parts.append(f"{tool.name}({arguments})")
        return "; ".join(parts) or "无可用工具"
