import asyncio
import base64
import copy
import hashlib
import json
from collections.abc import AsyncIterator, Callable
from contextlib import suppress
from uuid import uuid4

from desktopmate_agent.agent.graph import AgentGraph, AgentGraphUpdate
from desktopmate_agent.agent.intent import IntentDecision, IntentRouter
from desktopmate_agent.agent.state import AgentRunState
from desktopmate_agent.attachments.store import ALLOWED_IMAGE_TYPES, AttachmentStore
from desktopmate_agent.api.schemas import ServerEvent
from desktopmate_agent.knowledge.store import DocumentStore
from desktopmate_agent.memory.models import MemoryEntry, MemoryScope, MemoryStatus
from desktopmate_agent.memory.store import JsonlMemoryStore, RedisMemoryStore, SqliteMemoryStore
from desktopmate_agent.models.provider import ModelProvider
from desktopmate_agent.runtime.events import RuntimeEventBus
from desktopmate_agent.runtime.sessions import new_run_id, new_session_id
from desktopmate_agent.sessions.store import DEFAULT_TITLE, SessionStore
from desktopmate_agent.tools.base import RiskLevel
from desktopmate_agent.tools.permissions import PermissionRequestBroker
from desktopmate_agent.tools.registry import ToolRegistry
from desktopmate_agent.trace.events import TraceEvent
from desktopmate_agent.trace.store import JsonlTraceStore


SENSITIVE_MEMORY_KEYWORDS = (
    "api_key",
    "apikey",
    "secret",
    "token",
    "password",
    "passwd",
    "credential",
    "private_key",
)

MAX_MEMORY_KEY_LENGTH = 200
MAX_MEMORY_VALUE_LENGTH = 4_000
_SENSITIVE_TOOL_KEYS = {"content", "text"}
# 工具已禁用/移除时的事件 payload 占位（仅影响展示，不影响权限判定）
_UNKNOWN_RISK = RiskLevel.READ


class _UnknownTool:
    """工具已禁用/移除时的占位：只提供 risk_level 供事件 payload 使用。"""

    risk_level = _UNKNOWN_RISK


_UNKNOWN_TOOL = _UnknownTool()
SESSION_SUMMARY_KEY = "session_summary"


class AgentService:
    def __init__(
        self,
        *,
        model_provider: ModelProvider,
        tool_registry: ToolRegistry,
        trace_store: JsonlTraceStore,
        intent_router: IntentRouter | None = None,
        permission_broker: PermissionRequestBroker | None = None,
        memory_store: JsonlMemoryStore | RedisMemoryStore | SqliteMemoryStore | None = None,
        memory_user_id: str = "default_user",
        memory_project_id: str | None = None,
        memory_context_limit: int = 8,
        context_compression_enabled: bool = False,
        context_compression_threshold: int = 12,
        auto_memory_candidates: bool = False,
        auto_memory_candidates_limit: int = 3,
        session_store: SessionStore | None = None,
        attachment_store: AttachmentStore | None = None,
        provider_factory: Callable[[], ModelProvider] | None = None,
        runtime_events: RuntimeEventBus | None = None,
        knowledge_store: DocumentStore | None = None,
        skill_prompts: list[dict[str, str]] | None = None,
    ) -> None:
        self.model_provider = model_provider
        self.provider_factory = provider_factory
        # 用户导入并启用的 prompt skill（name → prompt），注入每轮系统提示词。
        self.skill_prompts = skill_prompts or []
        # provider 托管标记：仅当构造时提供了 factory 才启用自动刷新；
        # 一旦调用方直接替换 model_provider（如测试注入），刷新立即停用。
        self._managed_provider = model_provider if provider_factory is not None else None
        self.tool_registry = tool_registry
        knowledge_search_tool = tool_registry.safe_get("knowledge_search")
        self.runtime_events = runtime_events
        self.knowledge_store = knowledge_store or getattr(
            knowledge_search_tool,
            "document_store",
            None,
        )
        self.knowledge_search_tool = knowledge_search_tool if self.knowledge_store is not None else None
        self.trace_store = trace_store
        self.permission_broker = permission_broker
        self.session_store = session_store
        self.attachment_store = attachment_store
        self.memory_store = memory_store
        self.memory_user_id = memory_user_id
        self.memory_project_id = memory_project_id
        self.memory_context_limit = memory_context_limit
        self.context_compression_enabled = context_compression_enabled
        self.context_compression_threshold = context_compression_threshold
        self.auto_memory_candidates = auto_memory_candidates
        self.auto_memory_candidates_limit = auto_memory_candidates_limit
        self._pending_memory_candidates: dict[str, MemoryEntry] = {}
        self._session_message_counts: dict[str, int] = {}
        self.agent_graph = AgentGraph(
            model_provider=model_provider,
            tool_registry=tool_registry,
            intent_router=intent_router,
            skill_prompts=self.skill_prompts,
        )

    def refresh_provider(self) -> None:
        """每次 run 前刷新模型 provider，使 Settings 保存的 key/模型立即生效。

        只刷新"托管"的 provider：调用方直接替换 model_provider 后视为注入
        自定义 provider（测试/定制场景），不再自动覆盖。
        """
        if self._managed_provider is None or self.model_provider is not self._managed_provider:
            return
        new_provider = self.provider_factory()
        self.model_provider = new_provider
        self._managed_provider = new_provider
        set_model_provider = getattr(self.agent_graph.intent_router, "set_model_provider", None)
        if callable(set_model_provider):
            set_model_provider(new_provider)

    def _audit_tool_calls(
        self,
        tool_calls: list[dict[str, object]],
        run_id: str,
    ) -> list[dict[str, object]]:
        """Attach stable, UI-safe audit metadata without mutating graph state."""
        audited: list[dict[str, object]] = []
        for index, raw_call in enumerate(tool_calls, start=1):
            call = dict(raw_call)
            tool_name = str(call.get("name", ""))
            if tool_name:
                tool = self.tool_registry.safe_get(tool_name)
                if tool is None:
                    # 工具在执行中途被禁用/移除：跳过审计元数据而非崩溃整个 run
                    call["risk_level"] = "unknown"
                    call["requires_permission"] = False
                else:
                    call["risk_level"] = str(tool.risk_level)
                    call["requires_permission"] = tool.requires_permission
            call["id"] = str(call.get("id") or f"tool_{run_id}_{index}")
            audited.append(call)
        return audited

    async def stream_chat(
        self,
        *,
        text: str,
        session_id: str | None,
        mode: str = "agent",
        attachments: list[str] | None = None,
        artifact_id: str | None = None,
        workspace_context: dict[str, str] | None = None,
        project_id: str | None = None,
        surface: str = "main",
    ) -> AsyncIterator[ServerEvent]:
        """流式聊天（S2 起发布 runtime 事件，供悬浮桌宠订阅）。

        事件映射：``run_started``（首个事件）→ ``run_progress``
        （agent_message / tool_call_*）→ ``run_completed``（done）
        或 ``run_failed``（异常/取消）。未配置 event bus 时零开销透传。
        """
        run_id: str | None = None
        stream = self._stream_chat_impl(
            text=text,
            session_id=session_id,
            mode=mode,
            attachments=attachments,
            artifact_id=artifact_id,
            workspace_context=workspace_context,
            project_id=project_id,
            surface=surface,
        )
        active_session_id = session_id
        runtime_started = False
        try:
            async for event in stream:
                if run_id is None and event.run_id is not None:
                    run_id = event.run_id
                if event.session_id:
                    active_session_id = event.session_id
                    if self.runtime_events is not None and not runtime_started:
                        await self.runtime_events.append(
                            "run_started",
                            {
                                "session_id": event.session_id,
                                "run_id": run_id,
                                "mode": mode,
                            },
                        )
                        runtime_started = True
                if self.runtime_events is not None and run_id is not None:
                    payload = event.payload or {}
                    if event.type == "agent_message" and payload.get("text"):
                        await self.runtime_events.append(
                            "run_progress",
                            {"run_id": run_id, "stage": "answer", "text": payload["text"]},
                        )
                    elif event.type in {"tool_call_started", "tool_call_finished", "tool_call_failed"}:
                        await self.runtime_events.append(
                            "run_progress",
                            {
                                "run_id": run_id,
                                "stage": "tool",
                                "tool": payload.get("tool_name", ""),
                            },
                        )
                    elif event.type == "done":
                        await self.runtime_events.append(
                            "run_completed",
                            {"session_id": event.session_id, "run_id": run_id},
                        )
                yield event
        except asyncio.CancelledError:
            if active_session_id:
                self._persist_agent_message(
                    active_session_id,
                    "已取消当前任务。",
                    metadata={"run_status": "cancelled", "run_id": run_id, "surface": surface},
                )
            if self.runtime_events is not None and run_id is not None:
                await self.runtime_events.append(
                    "run_failed",
                    {"session_id": active_session_id, "run_id": run_id, "reason": "cancelled"},
                )
            raise
        except Exception as exc:
            if active_session_id:
                self._persist_agent_message(
                    active_session_id,
                    f"本次任务未完成：{exc}",
                    metadata={"run_status": "failed", "run_id": run_id, "surface": surface},
                )
            if self.runtime_events is not None and run_id is not None:
                await self.runtime_events.append(
                    "run_failed",
                    {"session_id": active_session_id, "run_id": run_id, "reason": str(exc)},
                )
            raise
        finally:
            # 显式关闭内层生成器：外层被 aclose()/GC 时保证内层同步关闭，
            # 避免内层生成器残留到事件循环关闭才被 finalizer 处理。
            await stream.aclose()

    async def _stream_chat_impl(
        self,
        *,
        text: str,
        session_id: str | None,
        mode: str = "agent",
        attachments: list[str] | None = None,
        artifact_id: str | None = None,
        workspace_context: dict[str, str] | None = None,
        project_id: str | None = None,
        surface: str = "main",
    ) -> AsyncIterator[ServerEvent]:
        self.refresh_provider()
        active_session_id = session_id or new_session_id()
        run_id = new_run_id()
        self._persist_user_message(
            active_session_id,
            text,
            mode=mode,
            attachments=attachments or [],
            workspace_context=workspace_context,
            project_id=project_id,
            surface=surface,
        )

        started = ServerEvent(type="agent_started", session_id=active_session_id, run_id=run_id)
        self._trace(started)
        yield started

        memory_command_result = self._try_memory_command(active_session_id, text)
        if memory_command_result is not None:
            message = ServerEvent(
                type="agent_message",
                session_id=active_session_id,
                run_id=run_id,
                payload={"text": memory_command_result},
            )
            self._persist_agent_message(
                active_session_id,
                memory_command_result,
                metadata={"run_id": run_id, "surface": surface},
            )
            self._trace(message)
            yield message
            done = ServerEvent(type="done", session_id=active_session_id, run_id=run_id)
            self._trace(done)
            yield done
            return

        state = AgentRunState(
            session_id=active_session_id,
            run_id=run_id,
            user_text=text,
            mode=mode,
            memory_context=self._load_memory_context(active_session_id),
            history=self._load_conversation_history(active_session_id),
            image_contents=self._resolve_image_contents(attachments or []),
            document_contexts=self._resolve_document_contents(attachments or []),
            artifact_id=artifact_id,
            workspace_context=workspace_context,
        )
        delta_queue: asyncio.Queue[str] = asyncio.Queue()
        reasoning_queue: asyncio.Queue[str] = asyncio.Queue()

        def delta_event(delta: str) -> ServerEvent:
            return ServerEvent(
                type="agent_delta",
                session_id=active_session_id,
                run_id=run_id,
                payload={"text": delta},
            )

        def reasoning_event(reasoning: str) -> ServerEvent:
            return ServerEvent(
                type="agent_thinking",
                session_id=active_session_id,
                run_id=run_id,
                payload={"text": reasoning},
            )

        graph = AgentGraph(
            model_provider=self.model_provider,
            tool_registry=self.tool_registry,
            intent_router=self.agent_graph.intent_router,
            skill_prompts=self.skill_prompts,
            on_delta=lambda delta_text: delta_queue.put_nowait(delta_text),
            on_reasoning=lambda reasoning_text: reasoning_queue.put_nowait(reasoning_text),
            on_intent=lambda decision, user_text: self._trace_intent(
                decision, user_text, run_id=run_id, session_id=active_session_id
            ),
        )
        graph_stream = graph.stream(state)
        updates_queue: asyncio.Queue[AgentGraphUpdate | None] = asyncio.Queue()
        permission_gate = asyncio.Event()

        async def pump_updates() -> None:
            try:
                async for item in graph_stream:
                    await updates_queue.put(item)
                    pending_tool = item.state.pending_tool_call
                    if item.node == "assistant" and pending_tool:
                        tool = self.tool_registry.safe_get(pending_tool["name"])
                        if tool is not None and (
                            tool.requires_permission
                            or tool.risk_level
                            in {
                                RiskLevel.WRITE,
                                RiskLevel.SYSTEM,
                                RiskLevel.EXTERNAL,
                            }
                        ):
                            # Do not let LangGraph advance to its tool node while
                            # the service is waiting for the user's approval.
                            await permission_gate.wait()
                            permission_gate.clear()
            finally:
                await updates_queue.put(None)

        pump_task = asyncio.create_task(pump_updates())
        try:
            while True:
                # 优先发积压的 thinking/delta，再等待新的 thinking、delta 或 graph 更新
                while not reasoning_queue.empty():
                    yield reasoning_event(reasoning_queue.get_nowait())
                while not delta_queue.empty():
                    yield delta_event(delta_queue.get_nowait())
                update_wait = asyncio.create_task(updates_queue.get())
                delta_wait = asyncio.create_task(delta_queue.get())
                reasoning_wait = asyncio.create_task(reasoning_queue.get())
                done, pending = await asyncio.wait(
                    {update_wait, delta_wait, reasoning_wait},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in pending:
                    task.cancel()
                if update_wait in done:
                    update = update_wait.result()
                    if update is None:
                        while not reasoning_queue.empty():
                            yield reasoning_event(reasoning_queue.get_nowait())
                        while not delta_queue.empty():
                            yield delta_event(delta_queue.get_nowait())
                        break
                    state = update.state
                elif reasoning_wait in done:
                    yield reasoning_event(reasoning_wait.result())
                    continue
                else:
                    # delta 先到：直接下发，继续等待下一轮
                    yield delta_event(delta_wait.result())
                    continue
                if update.node == "assistant" and state.pending_tool_call:
                    tool_call = state.pending_tool_call
                    tool = self.tool_registry.safe_get(tool_call["name"])
                    if tool is None:
                        # 工具在路由后、执行前被禁用：跳过权限请求，工具节点会返回"不可用"
                        permission_gate.set()
                        continue
                    tool_call_id = str(
                        tool_call.get("id") or f"tool_{run_id}_{len(state.tool_calls)}"
                    )
                    if tool.requires_permission or tool.risk_level in {
                        RiskLevel.WRITE,
                        RiskLevel.SYSTEM,
                        RiskLevel.EXTERNAL,
                    }:
                        permission_request = await self._create_tool_permission_request(
                            session_id=active_session_id,
                            run_id=run_id,
                            tool_call_id=tool_call_id,
                            tool_name=tool_call["name"],
                            risk_level=str(tool.risk_level),
                            args=tool_call["args"],
                        )
                        yield permission_request.request_event
                        permission_result = await self._wait_for_permission_decision(
                            permission_request
                        )
                        if permission_result.decision != "approved":
                            reason = permission_result.reason or "权限未批准"
                            audited_tool_calls = self._audit_tool_calls(state.tool_calls, run_id)
                            message = ServerEvent(
                                type="agent_message",
                                session_id=active_session_id,
                                run_id=run_id,
                                payload={
                                    "text": f"已取消执行 {tool_call['name']}：{reason}",
                                    "run_status": "permission_denied",
                                    "messages": [
                                        *state.messages,
                                        {
                                            "role": "assistant",
                                            "content": f"已取消执行 {tool_call['name']}：{reason}",
                                        },
                                    ],
                                    "tool_calls": audited_tool_calls,
                                    "tool_results": state.tool_results,
                                    "memory_context": state.memory_context,
                                    "citations": state.citations,
                                },
                            )
                            self._persist_agent_message(
                                active_session_id,
                                f"已取消执行 {tool_call['name']}：{reason}",
                                metadata={
                                    "tool_calls": audited_tool_calls,
                                    "tool_results": state.tool_results,
                                    "run_status": "permission_denied",
                                    "run_id": run_id,
                                    "surface": surface,
                                },
                            )
                            self._trace(message)
                            yield message
                            done = ServerEvent(
                                type="done", session_id=active_session_id, run_id=run_id
                            )
                            self._trace(done)
                            yield done
                            return
                        permission_gate.set()
                    tool_started = ServerEvent(
                        type="tool_call_started",
                        session_id=active_session_id,
                        run_id=run_id,
                        payload={
                            "tool_name": tool_call["name"],
                            "tool_call_id": tool_call_id,
                            "args": tool_call["args"],
                            "risk_level": str(tool.risk_level),
                            "requires_permission": tool.requires_permission,
                        },
                    )
                    self._trace(tool_started)
                    yield tool_started

                if update.node == "tool" and state.tool_calls and state.tool_results:
                    tool_call = state.tool_calls[-1]
                    tool_call_id = str(
                        tool_call.get("id") or f"tool_{run_id}_{len(state.tool_calls)}"
                    )
                    tool_result = state.tool_results[-1]
                    event_type = (
                        "tool_call_failed"
                        if tool_result.get("status") == "error"
                        else "tool_call_finished"
                    )
                    payload = {
                        "tool_name": tool_call["name"],
                        "tool_call_id": tool_call_id,
                        "result": tool_result,
                        "risk_level": str(
                            (
                                self.tool_registry.safe_get(tool_call["name"]) or _UNKNOWN_TOOL
                            ).risk_level
                        ),
                    }
                    if tool_result.get("status") == "error":
                        payload["error"] = str(tool_result.get("data", {}).get("error", ""))
                        error_kind = tool_result.get("data", {}).get("kind")
                        if error_kind:
                            payload["error_kind"] = str(error_kind)
                    tool_event = ServerEvent(
                        type=event_type,
                        session_id=active_session_id,
                        run_id=run_id,
                        payload=payload,
                    )
                    self._trace(tool_event)
                    yield tool_event
        finally:
            while not reasoning_queue.empty():
                reasoning_queue.get_nowait()
            while not delta_queue.empty():
                delta_queue.get_nowait()
            pump_task.cancel()
            with suppress(asyncio.CancelledError):
                await pump_task
            with suppress(asyncio.CancelledError):
                await graph_stream.aclose()

        self._session_message_counts[active_session_id] = (
            self._session_message_counts.get(active_session_id, 0) + 1
        )
        summary_written = False
        memory_candidates: list[dict[str, str]] = []
        # D4: 工具轮次也执行压缩/记忆抽取——transcript 会过滤工具摘要噪音。
        # （原 `not state.tool_calls` 条件导致工具密集会话永不压缩/抽取）
        summary_written = await self._maybe_compress_context(state, active_session_id)
        memory_candidates = await self._maybe_extract_memory_candidates(
            state, active_session_id
        )

        response_text = state.messages[-1]["content"]
        audited_tool_calls = self._audit_tool_calls(state.tool_calls, run_id)
        message = ServerEvent(
            type="agent_message",
            session_id=active_session_id,
            run_id=run_id,
            payload={
                "text": response_text,
                "messages": state.messages,
                "tool_calls": audited_tool_calls,
                "tool_results": state.tool_results,
                "memory_context": state.memory_context,
                "citations": state.citations,
            },
        )
        if summary_written:
            message.payload["session_summary"] = True
        if memory_candidates:
            message.payload["memory_candidates"] = memory_candidates
        self._persist_agent_message(
            active_session_id,
            response_text,
            metadata={
                "tool_calls": audited_tool_calls,
                "tool_results": state.tool_results,
                "citations": state.citations,
                "run_id": run_id,
                "surface": surface,
            },
        )
        self._trace(message)
        yield message

        done = ServerEvent(type="done", session_id=active_session_id, run_id=run_id)
        self._trace(done)
        yield done

    def _drain_delta_events(
        self,
        session_id: str,
        run_id: str,
        delta_queue: asyncio.Queue[str],
    ) -> list[ServerEvent]:
        events: list[ServerEvent] = []
        while not delta_queue.empty():
            delta = delta_queue.get_nowait()
            events.append(
                ServerEvent(
                    type="agent_delta",
                    session_id=session_id,
                    run_id=run_id,
                    payload={"text": delta},
                )
            )
        return events

    def _load_conversation_history(
        self,
        session_id: str,
        limit: int = 16,
    ) -> list[dict[str, str]]:
        """加载会话历史（不含当前这条用户消息），供模型获得多轮上下文。

        当前用户消息已在 stream_chat 开头持久化，因此取消息列表去掉最后一条。
        """
        if self.session_store is None:
            return []
        messages = self.session_store.list_messages(session_id)
        prior = messages[:-1] if len(messages) > 1 else []
        history = [{"role": message.role, "text": message.text} for message in prior[-limit:]]
        plan_snapshot = self._latest_plan_snapshot(prior)
        if plan_snapshot:
            history.append(
                {
                    "role": "system",
                    "text": (
                        "当前已制定计划（用户若要求更新，请输出完整替换计划）：\n"
                        f"{json.dumps(plan_snapshot, ensure_ascii=False)}"
                    ),
                }
            )
        return history

    @staticmethod
    def _latest_plan_snapshot(messages: list[object]) -> dict[str, object] | None:
        """Extract the most recent structured plan from persisted tool metadata."""
        for message in reversed(messages):
            metadata = getattr(message, "metadata", {})
            if not isinstance(metadata, dict):
                continue
            calls = metadata.get("tool_calls")
            results = metadata.get("tool_results")
            if not isinstance(calls, list) or not isinstance(results, list):
                continue
            for call, result in zip(reversed(calls), reversed(results), strict=False):
                if not isinstance(call, dict) or call.get("name") != "plan_draft":
                    continue
                if not isinstance(result, dict) or result.get("status") == "error":
                    continue
                data = result.get("data")
                plan = data.get("plan") if isinstance(data, dict) else None
                if isinstance(plan, dict):
                    return plan
        return None

    def _resolve_image_contents(self, attachments: list[str]) -> list[dict]:
        """把附件 id 解析为 OpenAI 多模态 content 块（data URL）。

        只处理图片附件；文档附件走 _resolve_document_contents 文本注入，
        不应进多模态路径（否则无视觉模型会误报"不支持图片识别"）。
        """
        if not attachments or self.attachment_store is None:
            return []
        contents: list[dict] = []
        for attachment_id in attachments:
            record = self.attachment_store.get(attachment_id)
            path = self.attachment_store.path_for(attachment_id)
            if record is None or path is None:
                continue
            if (record.mime or "").lower() not in ALLOWED_IMAGE_TYPES:
                continue
            data = path.read_bytes()
            contents.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{record.mime};base64,{base64.b64encode(data).decode('ascii')}"
                    },
                }
            )
        return contents

    def _resolve_document_contents(self, attachments: list[str]) -> list[dict[str, str]]:
        """把文档附件 id 解析为 [{filename, text}] 上下文块，供注入 prompt 直接对话。

        图片附件在此返回空（走 image_contents 多模态）；读取失败/超限的
        文档静默跳过（内容为空则不进上下文）。
        """
        if not attachments or self.attachment_store is None:
            return []
        contents: list[dict[str, str]] = []
        for attachment_id in attachments:
            record = self.attachment_store.get(attachment_id)
            if record is None:
                continue
            text = self.attachment_store.content_text(attachment_id)
            if not text.strip():
                continue
            contents.append({"filename": record.filename, "text": text})
        return contents

    def _persist_user_message(
        self,
        session_id: str,
        text: str,
        *,
        mode: str,
        attachments: list[str],
        workspace_context: dict[str, str] | None = None,
        surface: str = "main",
        project_id: str | None = None,
    ) -> None:
        if self.session_store is None:
            return
        session = self.session_store.ensure_session(
            session_id, mode=mode, project_id=project_id  # type: ignore[arg-type]
        )
        metadata: dict[str, object] = {}
        if attachments:
            metadata["attachments"] = attachments
        if workspace_context:
            metadata["workspace_context"] = workspace_context
        metadata["surface"] = surface
        self.session_store.add_message(session_id, role="user", text=text, metadata=metadata)
        if session.title == DEFAULT_TITLE:
            title = text if len(text) <= 34 else f"{text[:34]}..."
            self.session_store.update_session(session_id, title=title)

    def _persist_agent_message(
        self,
        session_id: str,
        text: str,
        *,
        metadata: dict[str, object] | None = None,
    ) -> None:
        if self.session_store is None:
            return
        self.session_store.add_message(
            session_id,
            role="assistant",
            text=text,
            metadata=metadata,
        )

    def _trace_intent(
        self,
        decision: IntentDecision,
        user_text: str,
        *,
        run_id: str,
        session_id: str,
    ) -> None:
        """意图路由埋点：记录来源(规则/模型)、置信度、最终意图，供流量分布与准确率分析。"""
        self.trace_store.append(
            TraceEvent(
                type="intent_routed",
                session_id=session_id,
                run_id=run_id,
                payload={
                    "text": user_text[:200],
                    "intent_type": decision.type.value,
                    "tool_name": decision.tool_name or "",
                    "confidence": decision.confidence,
                    "source": decision.source,
                    "reason": decision.reason or "",
                },
            )
        )

    def _trace(self, event: ServerEvent) -> None:
        if event.session_id is None or event.run_id is None:
            return
        traced_event = self._redact_for_trace(event)
        self.trace_store.append(
            TraceEvent(
                type=traced_event.type,
                session_id=traced_event.session_id,
                run_id=traced_event.run_id,
                payload=traced_event.payload,
            )
        )

    def _redact_for_trace(self, event: ServerEvent) -> ServerEvent:
        payload = copy.deepcopy(event.payload)
        for container_key in ("args", "result", "tool_results", "tool_calls"):
            if container_key in payload:
                payload[container_key] = self._redact_tree(payload[container_key])
        if isinstance(payload.get("memory_candidates"), list):
            payload["memory_candidates"] = [
                {key: ("<redacted>" if key == "value" else value) for key, value in item.items()}
                for item in payload["memory_candidates"]
            ]
        return ServerEvent(
            type=event.type,
            session_id=event.session_id,
            run_id=event.run_id,
            payload=payload,
        )

    def _redact_tree(self, value: object) -> object:
        if isinstance(value, dict):
            return {
                key: ("<redacted>" if key in _SENSITIVE_TOOL_KEYS else self._redact_tree(item))
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [self._redact_tree(item) for item in value]
        return value

    def _load_memory_context(self, session_id: str) -> list[dict[str, str]]:
        if self.memory_store is None:
            return []
        entries = self._rank_memory_context(
            self.memory_store.read_context(
                session_id=session_id,
                user_id=self.memory_user_id,
                project_id=self.memory_project_id,
            )
        )[: self.memory_context_limit]
        return [
            {
                "scope": entry.scope.value,
                "key": entry.key,
                "value": entry.value,
                "source": entry.source,
            }
            for entry in entries
        ]

    def _rank_memory_context(self, entries: list[MemoryEntry]) -> list[MemoryEntry]:
        scope_priority = {
            MemoryScope.SESSION: 0,
            MemoryScope.PROJECT: 1,
            MemoryScope.USER: 2,
        }

        def sort_key(indexed_entry: tuple[int, MemoryEntry]) -> tuple[float, ...]:
            index, entry = indexed_entry
            if entry.scope == MemoryScope.SESSION:
                return (scope_priority[entry.scope], index)
            return (
                scope_priority[entry.scope],
                -entry.updated_at.timestamp(),
                -entry.confidence,
                index,
            )

        return [entry for _, entry in sorted(enumerate(entries), key=sort_key)]

    def _try_memory_command(self, session_id: str, text: str) -> str | None:
        if self.memory_store is None:
            return None
        normalized = text.strip()
        if normalized == "/memories":
            return self._list_memories(session_id)
        if normalized.startswith("/forget "):
            return self._forget_memory(session_id, normalized.removeprefix("/forget ").strip())
        if normalized.startswith("/remember "):
            return self._remember_memory(session_id, normalized.removeprefix("/remember ").strip())
        return None

    def _remember_memory(self, session_id: str, raw_memory: str) -> str:
        if raw_memory.startswith("candidate "):
            return self._create_memory_candidate(
                session_id,
                raw_memory.removeprefix("candidate ").strip(),
            )
        if raw_memory.startswith("approve "):
            return self._approve_memory_candidate(
                session_id,
                raw_memory.removeprefix("approve ").strip(),
            )
        if raw_memory.startswith("reject "):
            return self._reject_memory_candidate(
                session_id,
                raw_memory.removeprefix("reject ").strip(),
            )

        scope, raw_memory = self._parse_memory_scope(raw_memory)
        key, separator, value = raw_memory.partition(" ")
        if not separator or not value.strip():
            return "记忆需要 key 和 value。"
        memory_key = key.strip()
        memory_value = value.strip()
        length_message = self._memory_length_error(memory_key, memory_value)
        if length_message:
            return length_message
        if self._looks_sensitive_memory(memory_key, memory_value):
            return "这条内容看起来包含敏感信息，未写入记忆。"
        updated = self.memory_store.upsert(
            MemoryEntry(
                scope=scope,
                subject_id=self._subject_id_for_scope(scope, session_id),
                session_id=session_id,
                key=memory_key,
                value=memory_value,
                source="user_command",
            )
        )
        action = "更新" if updated else "记住"
        return f"已{action} {memory_key}。"

    def _create_memory_candidate(self, session_id: str, raw_memory: str) -> str:
        scope, raw_memory = self._parse_memory_scope(raw_memory)
        key, separator, value = raw_memory.partition(" ")
        if not separator or not value.strip():
            return "候选记忆需要 key 和 value。"
        memory_key = key.strip()
        memory_value = value.strip()
        length_message = self._memory_length_error(memory_key, memory_value)
        if length_message:
            return length_message
        if self._looks_sensitive_memory(memory_key, memory_value):
            return "这条内容看起来包含敏感信息，未写入候选记忆。"

        candidate_id = f"mem_{uuid4().hex}"
        candidate = MemoryEntry(
            id=candidate_id,
            scope=scope,
            subject_id=self._subject_id_for_scope(scope, session_id),
            session_id=session_id,
            key=memory_key,
            value=memory_value,
            source="candidate_suggested",
            confidence=0.7,
            status=MemoryStatus.CANDIDATE,
        )
        self._save_memory_candidate(candidate_id, candidate)
        return f"候选记忆 {candidate_id}：{scope.value}.{memory_key} = {memory_value}"

    def _approve_memory_candidate(self, session_id: str, candidate_id: str) -> str:
        if isinstance(self.memory_store, SqliteMemoryStore):
            candidate = self.memory_store.resolve_candidate(
                candidate_id,
                session_id=session_id,
                approved=True,
            )
        else:
            candidate = self._pending_memory_candidates.pop(candidate_id, None)
            if candidate is not None:
                approved_candidate = candidate.model_copy(update={"source": "candidate_approved"})
                self.memory_store.upsert(approved_candidate)
                candidate = approved_candidate
        if candidate is None:
            return "未找到这条候选记忆。"
        return f"已记住 {candidate.key}。"

    def _reject_memory_candidate(self, session_id: str, candidate_id: str) -> str:
        if isinstance(self.memory_store, SqliteMemoryStore):
            candidate = self.memory_store.resolve_candidate(
                candidate_id,
                session_id=session_id,
                approved=False,
            )
        else:
            candidate = self._pending_memory_candidates.pop(candidate_id, None)
        if candidate is None:
            return "未找到这条候选记忆。"
        return f"已忽略 {candidate.key}。"

    def _looks_sensitive_memory(self, key: str, value: str) -> bool:
        lowered_key = key.lower()
        lowered_value = value.lower()
        return any(keyword in lowered_key for keyword in SENSITIVE_MEMORY_KEYWORDS) or any(
            marker in lowered_value
            for marker in ("sk-", "-----begin private key-----", "xoxb-", "ghp_")
        )

    def _memory_length_error(self, key: str, value: str) -> str | None:
        if len(key) > MAX_MEMORY_KEY_LENGTH:
            return f"记忆 key 不能超过 {MAX_MEMORY_KEY_LENGTH} 字符。"
        if len(value) > MAX_MEMORY_VALUE_LENGTH:
            return f"记忆 value 不能超过 {MAX_MEMORY_VALUE_LENGTH} 字符。"
        return None

    async def _maybe_compress_context(self, state: AgentRunState, session_id: str) -> bool:
        if not self.context_compression_enabled or self.memory_store is None:
            return False
        message_count = self._session_message_counts.get(session_id, 0)
        if (
            message_count < self.context_compression_threshold
            or message_count % self.context_compression_threshold != 0
        ):
            return False
        summary = (
            await self.model_provider.complete(
                self._build_compression_prompt(
                    self._transcript(state.messages, skip_tool_summaries=True)
                )
            )
        ).strip()
        if not summary:
            return False
        self.memory_store.upsert(
            MemoryEntry(
                scope=MemoryScope.SESSION,
                session_id=session_id,
                key=SESSION_SUMMARY_KEY,
                value=summary,
                source="auto_summary",
                confidence=0.8,
            )
        )
        return True

    async def _maybe_extract_memory_candidates(
        self,
        state: AgentRunState,
        session_id: str,
    ) -> list[dict[str, str]]:
        if not self.auto_memory_candidates or self.memory_store is None:
            return []
        response = await self.model_provider.complete(
            self._build_candidate_prompt(
                self._transcript(state.messages, skip_tool_summaries=True)
            )
        )
        try:
            raw_items = json.loads(response)
        except json.JSONDecodeError:
            return []
        if not isinstance(raw_items, list):
            return []

        candidates: list[dict[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for item in raw_items[: self.auto_memory_candidates_limit]:
            if not isinstance(item, dict):
                continue
            scope_raw = str(item.get("scope", "user"))
            if scope_raw not in {scope.value for scope in MemoryScope}:
                continue
            key = str(item.get("key", "")).strip()
            value = str(item.get("value", "")).strip()
            if not key or not value or self._memory_length_error(key, value):
                continue
            if self._looks_sensitive_memory(key, value):
                continue
            dedupe_key = (scope_raw, key)
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            scope = MemoryScope(scope_raw)
            candidate_id = f"mem_{uuid4().hex}"
            candidate = MemoryEntry(
                id=candidate_id,
                scope=scope,
                subject_id=self._subject_id_for_scope(scope, session_id),
                session_id=session_id,
                key=key,
                value=value,
                source="auto_candidate",
                confidence=0.6,
                status=MemoryStatus.CANDIDATE,
            )
            self._save_memory_candidate(candidate_id, candidate)
            self._maybe_consolidate_memory_candidate(key, value)
            candidates.append(
                {
                    "candidate_id": candidate_id,
                    "scope": scope_raw,
                    "key": key,
                    "value": value,
                }
            )
        return candidates

    def _maybe_consolidate_memory_candidate(self, key: str, value: str) -> None:
        if self.knowledge_store is None:
            return
        content = f"{key}: {value}"
        sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest()
        if self.knowledge_store.find_by_sha256(sha256):
            return
        occurrences = sum(
            1
            for candidate in self._pending_memory_candidates.values()
            if candidate.key == key and candidate.value == value
        )
        if occurrences < 3:
            return
        document = self.knowledge_store.add_document(
            name=f"memory_{hashlib.sha256(content.encode('utf-8')).hexdigest()[:12]}.md",
            file_type="MD",
            size=len(content.encode("utf-8")),
            source="auto_consolidation",
            importance=0.7,
            sha256=sha256,
        )
        self.knowledge_store.replace_chunks(
            document.id,
            [
                {
                    "id": f"chunk_{uuid4().hex}",
                    "chunk_index": 0,
                    "text": content,
                    "token_count": max(1, len(content) // 4),
                    "metadata_json": json.dumps(
                        {
                            "source": "auto_consolidation",
                            "title": document.name,
                            "importance": 0.7,
                        },
                        ensure_ascii=False,
                    ),
                }
            ],
        )
        self.knowledge_store.set_document_status(document.id, status="indexed", chunk_count=1)
        refresh_index = getattr(self.knowledge_search_tool, "refresh_index", None)
        if callable(refresh_index):
            refresh_index()

    def _save_memory_candidate(self, candidate_id: str, candidate: MemoryEntry) -> None:
        self._pending_memory_candidates[candidate_id] = candidate
        if isinstance(self.memory_store, SqliteMemoryStore):
            self.memory_store.upsert(candidate)

    def _transcript(
        self,
        messages: list[dict[str, str]],
        *,
        skip_tool_summaries: bool = False,
    ) -> str:
        """把消息列表转成对话文本。

        ``skip_tool_summaries=True`` 时过滤带 ``tool_summary`` 标记的工具摘要消息
        （压缩/记忆抽取专用——避免工具噪音污染摘要与记忆候选）。
        """
        filtered = messages
        if skip_tool_summaries:
            filtered = [
                message
                for message in messages
                if not message.get("tool_summary")
            ]
        return "\n".join(f"{message['role']}: {message['content']}" for message in filtered)

    def _build_compression_prompt(self, transcript: str) -> str:
        return (
            "你是 DesktopMate 的会话压缩器。把下面的对话压缩成不超过 150 字的中文摘要。\n"
            "只保留稳定事实、用户偏好、任务目标和已完成的动作，不要编造内容。\n\n"
            f"<conversation>\n{transcript}\n</conversation>\n\n摘要："
        )

    def _build_candidate_prompt(self, transcript: str) -> str:
        return (
            "你是 DesktopMate 的记忆抽取器。从下面的对话中提取值得记住的稳定事实或用户偏好。\n"
            "只返回 JSON 数组，不要解释，不要代码块。数组元素格式：\n"
            '[{"scope": "session|user|project", "key": "short_english_key", "value": "简短中文值"}]\n'
            f"最多返回 {self.auto_memory_candidates_limit} 条。临时、敏感或不明确的信息不要抽取。\n\n"
            f"<conversation>\n{transcript}\n</conversation>"
        )

    def _list_memories(self, session_id: str) -> str:
        entries = self.memory_store.read_context(
            session_id=session_id,
            user_id=self.memory_user_id,
            project_id=self.memory_project_id,
        )
        if not entries:
            return "当前没有可用记忆。"
        return "\n".join(f"- {entry.scope.value}.{entry.key}: {entry.value}" for entry in entries)

    def _forget_memory(self, session_id: str, raw_memory: str) -> str:
        scope, raw_memory = self._parse_memory_scope(raw_memory)
        key = raw_memory.strip()
        if not key:
            return "删除记忆需要 scope 和 key。"
        deleted_count = self.memory_store.delete(scope, self._subject_id_for_scope(scope, session_id), key)
        return f"已删除 {deleted_count} 条记忆。"

    def _parse_memory_scope(self, raw_memory: str) -> tuple[MemoryScope, str]:
        first, separator, rest = raw_memory.partition(" ")
        if separator and first in {scope.value for scope in MemoryScope}:
            return MemoryScope(first), rest.strip()
        return MemoryScope.USER, raw_memory

    def _subject_id_for_scope(self, scope: MemoryScope, session_id: str) -> str:
        if scope == MemoryScope.SESSION:
            return session_id
        if scope == MemoryScope.PROJECT:
            return self.memory_project_id or "default_project"
        return self.memory_user_id

    async def _create_tool_permission_request(
        self,
        *,
        session_id: str,
        run_id: str,
        tool_call_id: str,
        tool_name: str,
        risk_level: str,
        args: dict,
    ) -> "_PermissionRequestContext":
        review: dict[str, object] | None = None
        try:
            tool = self.tool_registry.safe_get(tool_name)
            if tool is not None:
                review = await tool.approval_preview(args)
        except Exception as exc:  # noqa: BLE001
            review = {"ready": False, "message": f"无法生成审核预览：{exc}"}
        if self.permission_broker is None:
            request_event = ServerEvent(
                type="permission_request",
                session_id=session_id,
                run_id=run_id,
                payload={
                    "tool_name": tool_name,
                    "tool_call_id": tool_call_id,
                    "risk_level": risk_level,
                    "summary": f"请求执行工具 {tool_name}",
                    "args": args,
                    "review": review,
                },
            )
            self._trace(request_event)
            return _PermissionRequestContext(
                request_event=request_event,
                request_id=None,
            )

        request = self.permission_broker.create_request(
            session_id=session_id,
            run_id=run_id,
            risk_level=risk_level,
            summary=f"请求执行工具 {tool_name}",
            payload={"tool_name": tool_name, "tool_call_id": tool_call_id, "args": args, "review": review},
        )
        request_payload = request.model_dump(mode="json")
        request_event = ServerEvent(
            type="permission_request",
            session_id=session_id,
            run_id=run_id,
            payload={
                **request_payload,
                "tool_name": tool_name,
                "tool_call_id": tool_call_id,
                "args": args,
                "review": review,
            },
        )
        self._trace(request_event)
        return _PermissionRequestContext(
            request_event=request_event,
            request_id=request.request_id,
        )

    async def _wait_for_permission_decision(
        self,
        request: "_PermissionRequestContext",
    ) -> "_PermissionResult":
        if self.permission_broker is None or request.request_id is None:
            return _PermissionResult(decision="denied", reason="权限系统未配置")

        decision = await self.permission_broker.wait_for_decision(request.request_id)
        return _PermissionResult(decision=decision.decision, reason=decision.reason)


class _PermissionRequestContext:
    def __init__(self, *, request_event: ServerEvent, request_id: str | None) -> None:
        self.request_event = request_event
        self.request_id = request_id


class _PermissionResult:
    def __init__(self, *, decision: str, reason: str | None) -> None:
        self.decision = decision
        self.reason = reason
