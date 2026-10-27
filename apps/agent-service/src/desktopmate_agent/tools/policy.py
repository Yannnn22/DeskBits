"""Central policy for tool visibility and execution modes."""

from __future__ import annotations


# 统一 Agent 模式后不再区分 chat/plan，mode 参数仅作向后兼容（旧客户端仍可能传入）。
AGENT_MODE = "agent"


def normalized_mode(mode: str) -> str:
    return AGENT_MODE


def mode_instruction(mode: str) -> str:
    return (
        "统一 Agent 模式：普通问题直接回答；用户明确要求规划时生成计划；"
        "仅在明确要求操作时调用工具，高风险操作仍需人工确认。"
    )
