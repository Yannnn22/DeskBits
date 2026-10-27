"""多 Agent 架构的 Worker 抽象。

Worker = AgentGraph + 定制 system_prompt + 工具白名单 + 输出约束。

设计要点：
- 工具白名单：从主注册表按名字筛选（共享工具实例，避免重复实例化带依赖工具）。
  只读工具可安全共享（如 knowledge_search 有并发锁）；写工具由调用方自行斟酌。
- 模型：复用主 Agent 的 provider（保持同一模型配置）。
- 输出格式：text（默认，取最后一条 assistant 消息）或 json（要求模型输出
  结构化 JSON，供 DAG 依赖链下游消费）。
- 与 DelegateOrchestrateTool 的 runner 签名兼容：`async (goal, context) -> str`，
  可直接替换现有 run_sub_agent 或作为更定制化的 worker 使用。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from desktopmate_agent.agent.graph import AgentGraph
from desktopmate_agent.agent.intent import IntentDecision, IntentRouter, IntentType
from desktopmate_agent.agent.state import AgentRunState
from desktopmate_agent.models.provider import ModelProvider
from desktopmate_agent.tools.registry import ToolRegistry


class WorkerError(Exception):
    """Worker 执行失败（可展示给用户）。"""


class DirectIntentRouter(IntentRouter):
    """Worker 专用直通路由器：不做启发式/编排路由。

    Worker 收到的是编排器派发的任务，应直接执行（模型直接回答，
    或按需调用白名单工具）。若走通用路由，子任务文本里的关键词
    （如"调研"）会误触发 delegate_pipeline 等编排工具，造成递归/拒绝。
    """

    def __init__(
        self,
        *,
        model_provider: ModelProvider,
        tool_registry: ToolRegistry,
    ) -> None:
        self.model_provider = model_provider
        self.tool_registry = tool_registry

    async def route(
        self,
        text: str,
        *,
        mode: str = "agent",
        observation: str = "",
    ) -> IntentDecision:
        # 工具刚执行完：需要决定是否继续调用工具。只允许调用白名单工具，
        # 且由模型自行判断；其余一律直接回答（worker 输出最终结果）。
        if observation:
            from desktopmate_agent.agent.intent import ModelIntentRouter

            model_router = ModelIntentRouter(
                model_provider=self.model_provider,
                tool_registry=self.tool_registry,
            )
            decision = await model_router.route(text, mode=mode, observation=observation)
            # 保险：模型若返回白名单外的工具名（如编排工具），一律降级为直接回答。
            if (
                decision.type == IntentType.TOOL_CALL
                and not self.tool_registry.has(str(decision.tool_name or ""))
            ):
                return IntentDecision(
                    type=IntentType.DIRECT_RESPONSE,
                    confidence=decision.confidence,
                    reason="worker 不允许调用白名单外的工具",
                    source="worker",
                )
            return decision
        # 无观察（任务初始）：直接让模型完成回答，不路由工具。
        return IntentDecision(
            type=IntentType.DIRECT_RESPONSE,
            confidence=1.0,
            reason="worker 直接执行任务",
            source="worker",
        )


class AgentWorker:
    """一个可独立执行任务的 Worker 代理。

    参数：
        name: Worker 名称（如 "researcher" / "writer"），用于日志与错误信息。
        system_prompt: 该 Worker 的角色指令。
        tool_names: 允许使用的工具白名单（主注册表中的名字）。
        model_provider: 模型提供者（与主 Agent 共享配置）。
        tool_registry: 主注册表（从中按白名单筛选工具实例）。
        output_format: "text" 返回文本；"json" 要求结构化 JSON 输出。
        max_steps: 工具调用步数上限（透传给 AgentGraph）。
        on_delta / on_reasoning: 可选流式回调（透传）。
    """

    def __init__(
        self,
        *,
        name: str,
        system_prompt: str,
        tool_names: list[str],
        model_provider: ModelProvider,
        tool_registry: ToolRegistry,
        output_format: str = "text",
        on_delta: Callable[[str], None] | None = None,
        on_reasoning: Callable[[str], None] | None = None,
    ) -> None:
        if output_format not in {"text", "json"}:
            raise ValueError(f"output_format 必须是 text|json，收到 {output_format!r}")
        self.name = name
        self.output_format = output_format

        # 从主注册表筛选白名单工具（共享实例，只读工具安全共享）。
        # 检查"已注册"用 list()（has() 会因禁用返回 False，白名单应容忍禁用工具）。
        registered_names = {tool.name for tool in tool_registry.list()}
        unknown = [n for n in tool_names if n not in registered_names]
        if unknown:
            raise ValueError(f"Worker {name} 引用了未注册工具: {unknown}")
        worker_registry = ToolRegistry()
        tools_by_name = {tool.name: tool for tool in tool_registry.list()}
        for tool_name in tool_names:
            tool = tools_by_name[tool_name]  # 已确认注册
            worker_registry.register(tool)
            worker_registry.set_enabled(tool_name, tool_registry.is_enabled(tool_name))

        # 结构化输出要求模型只输出 JSON，靠 system_prompt 尾部指令约束
        effective_prompt = system_prompt
        if output_format == "json":
            effective_prompt = (
                f"{system_prompt}\n\n"
                "输出要求：你的最终回答必须是一个合法 JSON 对象，"
                "不要输出 JSON 之外的任何内容（不要 markdown 代码块标记，不要解释）。"
            )

        self.graph = AgentGraph(
            model_provider=model_provider,
            tool_registry=worker_registry,
            intent_router=DirectIntentRouter(
                model_provider=model_provider,
                tool_registry=worker_registry,
            ),
            system_prompt=effective_prompt,
            on_delta=on_delta,
            on_reasoning=on_reasoning,
        )

    async def run(self, goal: str, context: str = "") -> str:
        """执行任务，返回最终输出。

        参数：
            goal: 任务目标（模型看到的用户指令）。
            context: 背景上下文（注入为"背景上下文"前缀）。
        """
        payload = goal if not context else f"背景上下文：\n{context}\n\n任务：\n{goal}"
        state = AgentRunState(
            session_id=f"w-{self.name}-{id(self):x}",
            run_id=f"w-{self.name}-{id(self):x}",
            user_text=payload,
        )
        try:
            result = await self.graph.run(state)
        except Exception as exc:
            raise WorkerError(f"Worker {self.name} 执行失败: {exc}") from exc

        for message in reversed(result.messages):
            if message.get("role") == "assistant":
                content = str(message.get("content", "")).strip()
                if content:
                    return content
        return "(worker 无输出)"

    # --- DelegateOrchestrateTool runner 兼容签名 ---
    def as_runner(self) -> Callable[[str, str], Awaitable[str]]:
        """返回 `async (goal, context) -> str` 签名，可直接作为 runner 传入。"""
        return self.run
