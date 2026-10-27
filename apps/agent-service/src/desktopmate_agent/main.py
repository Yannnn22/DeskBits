import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from desktopmate_agent.agent.graph import AgentGraph
from desktopmate_agent.agent.service import AgentService
from desktopmate_agent.artifacts.store import ArtifactStore
from desktopmate_agent.attachments.store import AttachmentStore
from desktopmate_agent.api.artifacts import create_artifacts_router
from desktopmate_agent.api.attachments import create_attachment_router
from desktopmate_agent.api.errors import register_error_handlers
from desktopmate_agent.api.fs import create_fs_router
from desktopmate_agent.api.files import create_files_router
from desktopmate_agent.api.health import create_health_router
from desktopmate_agent.api.knowledge import create_knowledge_router
from desktopmate_agent.api.projects import create_projects_router
from desktopmate_agent.api.profile import create_profile_router
from desktopmate_agent.api.reminders import create_reminders_router
from desktopmate_agent.api.tasks import create_tasks_router
from desktopmate_agent.api.routes import create_router
from desktopmate_agent.api.runtime_events import create_runtime_router
from desktopmate_agent.api.sessions import create_session_router
from desktopmate_agent.api.settings import create_settings_router
from desktopmate_agent.api.skills import create_skills_router
from desktopmate_agent.api.speech import create_speech_router
from desktopmate_agent.api.storage import create_storage_router
from desktopmate_agent.api.traces import create_trace_router
from desktopmate_agent.api.websocket import create_websocket_router
from desktopmate_agent.config import Settings, get_settings
from desktopmate_agent.knowledge.service import DocumentService
from desktopmate_agent.knowledge.store import DocumentStore
from desktopmate_agent.memory.store import JsonlMemoryStore, RedisMemoryStore, SqliteMemoryStore
from desktopmate_agent.models.provider import create_intent_router, create_model_provider
from desktopmate_agent.profile.store import ProfileFactsStore
from desktopmate_agent.rag.embeddings import create_embedding_provider
from desktopmate_agent.reminders.service import ReminderScheduler, ReminderService
from desktopmate_agent.reminders.store import ReminderStore
from desktopmate_agent.projects.store import ProjectStore
from desktopmate_agent.runtime.events import RuntimeEventBus
from desktopmate_agent.runtime.runs import RunRegistry
from desktopmate_agent.sessions.store import SessionStore
from desktopmate_agent.settings.store import SettingsStore
from desktopmate_agent.skills.store import SkillPackageStore
from desktopmate_agent.storage.service import StorageService
from desktopmate_agent.speech.stt import WhisperSTT
from desktopmate_agent.tasks.store import TaskStore
from desktopmate_agent.tools.delegate_tools import (
    DelegateBatchTool,
    DelegateOrchestrateTool,
    DelegatePipelineTool,
    DelegateTaskTool,
    SubTask,
    parse_decomposition,
)
from desktopmate_agent.tools.file_tools import (
    DraftWriteTool,
    FileDiffTool,
    FilePatchTool,
    ReadFileTool,
    SearchFilesTool,
)
from desktopmate_agent.tools.knowledge_tools import (
    KnowledgeEvaluateTool,
    KnowledgeRefreshTool,
    KnowledgeSaveTool,
    KnowledgeSearchTool,
    KnowledgeSummarizeTool,
)
from desktopmate_agent.tools.permissions import PermissionRequestBroker
from desktopmate_agent.tools.plan_tools import PlanDraftTool
from desktopmate_agent.tools.registry import ToolRegistry
from desktopmate_agent.tools.artifact_tools import ArtifactDraftTool, ArtifactRevisionTool
from desktopmate_agent.tools.task_tools import (
    ReminderCreateTool,
    TaskConfirmTool,
    TaskDraftTool,
    TaskCreateTool,
    TaskListTool,
    TaskUpdateTool,
)
from desktopmate_agent.trace.store import JsonlTraceStore
from desktopmate_agent.tools.web_search import WebSearchTool
from desktopmate_agent.tools.web_fetch import WebFetchTool

def create_app(
    trace_dir: Path | None = None,
    settings: Settings | None = None,
    key_tester: object | None = None,
    model_catalog: object | None = None,
    model_tester: object | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    active_trace_dir = trace_dir or settings.trace_dir
    auth_token = settings.auth_token or secrets.token_urlsafe(32)
    permission_broker = PermissionRequestBroker()
    tool_registry = ToolRegistry()
    tool_registry.register(
        SearchFilesTool(
            allowed_roots=settings.allowed_root_paths,
            max_search_files=settings.max_search_files,
        )
    )
    tool_registry.register(
        ReadFileTool(
            allowed_roots=settings.allowed_root_paths,
            max_read_bytes=settings.max_read_bytes,
        )
    )
    embedding_provider = create_embedding_provider(
        provider_name=settings.embedding_provider,
        dimensions=settings.embedding_dimensions,
        api_key=settings.embedding_api_key,
        base_url=settings.embedding_base_url,
        model=settings.embedding_model,
    )
    embedding_id = f"{settings.embedding_provider}:{settings.embedding_model}"
    knowledge_document_store = DocumentStore(settings.knowledge_db_path)
    knowledge_search_tool = KnowledgeSearchTool(
        knowledge_root=settings.knowledge_dir,
        milvus_enabled=settings.milvus_enabled,
        milvus_collection_name=settings.milvus_collection_name,
        milvus_uri=settings.milvus_uri,
        embedding_dimensions=settings.embedding_dimensions,
        embedding_provider=embedding_provider,
        document_store=knowledge_document_store,
        embedding_id=embedding_id,
    )
    tool_registry.register(knowledge_search_tool)
    tool_registry.register(KnowledgeRefreshTool(search_tool=knowledge_search_tool))
    tool_registry.register(KnowledgeEvaluateTool(search_tool=knowledge_search_tool))
    tool_registry.register(
        DraftWriteTool(
            allowed_roots=settings.allowed_root_paths,
            draft_root=settings.draft_dir,
        )
    )
    tool_registry.register(FileDiffTool(allowed_roots=settings.allowed_root_paths))
    tool_registry.register(FilePatchTool(allowed_roots=settings.allowed_root_paths))
    settings_store = SettingsStore(settings.settings_dir)
    tool_registry.register(
        WebSearchTool(
            api_key=lambda: settings_store.get_api_key("tavily") or settings.tavily_api_key,
            base_url=settings.tavily_base_url,
        )
    )
    # 补注册：web_fetch（读取网页正文，供研究类场景使用）
    tool_registry.register(WebFetchTool())
    _apply_persisted_skills(settings_store, tool_registry)

    # Tool Skill 启动恢复：重新加载已安装的 tool 技能注册为工具
    # （启停状态由 _apply_persisted_skills 统一恢复，顺序：先注册后恢复开关）
    skill_store = SkillPackageStore(settings.skills_dir)
    for package in skill_store.installed():
        if package.type != "tool":
            continue
        try:
            from desktopmate_agent.skills.loader import load_tool_skill

            tool = load_tool_skill(package, Path(package.install_path))
            if not tool_registry.has(tool.name):
                tool_registry.register(tool)
                tool_registry.set_enabled(tool.name, False)
        except Exception:
            # 单个技能加载失败不阻断启动，用户可在技能页重新导入
            continue
    _apply_persisted_skills(settings_store, tool_registry)
    model_provider = create_model_provider(settings, settings_store)
    memory_store = _create_memory_store(settings)
    session_store = SessionStore(settings.session_db_path)
    project_store = ProjectStore(settings.app_data_dir / "projects.db")
    artifact_store = ArtifactStore(settings.session_db_path)
    trace_store = JsonlTraceStore(
        active_trace_dir,
        retention_days=settings.trace_retention_days,
    )
    runtime_bus = RuntimeEventBus(settings.app_data_dir / "runtime_events.jsonl")
    reminder_store = ReminderStore(settings.knowledge_db_path)
    reminder_service = ReminderService(reminder_store, runtime_events=runtime_bus)
    reminder_scheduler = ReminderScheduler(reminder_service)
    task_store = TaskStore(settings.knowledge_db_path)
    task_confirm_tool = TaskConfirmTool(
        store=task_store,
        reminder_service=reminder_service,
        memory_store=memory_store,
    )
    attachment_store = AttachmentStore(settings.uploads_dir)
    storage_service = StorageService(
        settings=settings,
        attachment_store=attachment_store,
        trace_store=trace_store,
        runtime_events=runtime_bus,
    )
    document_service = DocumentService(
        store=knowledge_document_store,
        originals_dir=settings.knowledge_dir / "originals",
        extracted_dir=settings.knowledge_dir / "extracted",
        search_tool=knowledge_search_tool,
        max_upload_bytes=settings.max_upload_bytes,
        embedding_provider=embedding_provider,
        embedding_id=embedding_id,
    )
    tool_registry.register(KnowledgeSaveTool(saver=document_service.save_chat_note))
    tool_registry.register(KnowledgeSummarizeTool(summarizer=document_service.summarize_document))
    tool_registry.register(TaskCreateTool(store=task_store))
    tool_registry.register(TaskDraftTool())
    tool_registry.register(task_confirm_tool)
    tool_registry.register(TaskUpdateTool(store=task_store))
    tool_registry.register(TaskListTool(store=task_store))
    tool_registry.register(ReminderCreateTool(service=reminder_service))
    tool_registry.register(PlanDraftTool())

    async def compose_artifact(prompt: str) -> str:
        # 每次取当前设置，模型切换后生成候选文档无需重启服务。
        provider = create_model_provider(settings, settings_store)
        return await provider.complete(prompt)

    tool_registry.register(ArtifactDraftTool(store=artifact_store, compose=compose_artifact))
    tool_registry.register(ArtifactRevisionTool(store=artifact_store, compose=compose_artifact))

    # 只读子代理注册表：子代理可搜索/读/查知识库，绝无写工具（不能委派、不能改文件）
    # 设计决策（2026-08-09 审查确认）：
    # - knowledge_search_tool 与主 registry 共享实例是刻意的：子代理必须看到最新索引，
    #   一致性由 KnowledgeSearchTool._index_lock 保证（并发安全，仅可能互相等待）。
    # - 每次 run_sub_agent 新建 provider 也是刻意的：跟随用户即时切换的模型配置，
    #   子代理调用频率低（显式 delegate），client 重建开销可接受。
    sub_registry = ToolRegistry()
    for sub_tool in (
        knowledge_search_tool,
        KnowledgeSummarizeTool(summarizer=document_service.summarize_document),
        SearchFilesTool(
            allowed_roots=settings.allowed_root_paths,
            max_search_files=settings.max_search_files,
        ),
        ReadFileTool(
            allowed_roots=settings.allowed_root_paths,
            max_read_bytes=settings.max_read_bytes,
        ),
        FileDiffTool(allowed_roots=settings.allowed_root_paths),
    ):
        sub_registry.register(sub_tool)

    _SUB_SYSTEM_PROMPT = (
        "你是 DeskBits 的子任务代理，负责完成父任务派发的子任务。"
        "你只能读取（搜索文件、读文件、查知识库），不能写入或修改任何内容。"
        "输出简洁、直接、只报告任务结论，不要客套，不要提及你是子代理。"
    )

    async def run_sub_agent(goal: str, context: str) -> str:
        from desktopmate_agent.agent.state import AgentRunState
        from desktopmate_agent.agent.intent import ModelIntentRouter

        # Keep sub-agents on the same saved model/API configuration as the
        # primary run. They intentionally receive the read-only registry only.
        provider = create_model_provider(settings, settings_store)
        graph = AgentGraph(
            model_provider=provider,
            tool_registry=sub_registry,
            intent_router=ModelIntentRouter(
                model_provider=provider,
                tool_registry=sub_registry,
            ),
            system_prompt=_SUB_SYSTEM_PROMPT,
        )
        payload = f"背景上下文：\n{context}\n\n子任务：\n{goal}"
        state = AgentRunState(
            session_id=f"sub-{uuid4().hex[:12]}",
            run_id=f"sub-{uuid4().hex}",
            user_text=payload,
        )
        result = await graph.run(state)
        for message in reversed(result.messages):
            if message.get("role") == "assistant":
                return str(message.get("content", "")).strip() or "(子代理无输出)"
        return "(子代理无输出)"

    async def emit_subtask_progress(payload: dict) -> None:
        await runtime_bus.append("run_progress", {"stage": "subtask", **payload})

    tool_registry.register(DelegateTaskTool(runner=run_sub_agent))
    tool_registry.register(
        DelegateBatchTool(
            runner=run_sub_agent,
            progress_callback=emit_subtask_progress,
        )
    )

    async def decompose_request(request: str) -> list[SubTask]:
        prompt = (
            "你是任务分解器。把下面的请求拆成最多 3 个可并行、互不依赖、只读的子任务"
            "（子代理能搜索文件、读文件、查知识库，不能写入）。\n"
            "每个子任务的 goal 必须是给子代理的可执行指令，开头标注类型：\n"
            '- 需要查文档/资料/知识库内容的：goal 以「在个人知识库中查询：」开头；\n'
            '- 需要查磁盘文件/目录的：goal 以「在文件系统中查找：」开头。\n'
            "只输出一个 JSON 数组，不要任何解释。每个元素形如 "
            '{"goal": "在个人知识库中查询：淘宝在 test.md 里是什么风格", '
            '"context": "背景或约束（可为空字符串）"}。\n'
            f"请求：{request}"
        )
        provider = create_model_provider(settings, settings_store)
        raw = await provider.complete(prompt)
        return parse_decomposition(raw, fallback_request=request)

    tool_registry.register(
        DelegateOrchestrateTool(
            decomposer=decompose_request,
            runner=run_sub_agent,
            progress_callback=emit_subtask_progress,
        )
    )

    # ---- 多 Agent Worker 注册（1.2 结构化任务协议）----
    # 复用同一个 provider 工厂，worker 跟随用户当前模型配置。
    from desktopmate_agent.agents.worker import AgentWorker

    def make_worker(
        name: str,
        system_prompt: str,
        tool_names: list[str],
        output_format: str = "text",
    ) -> AgentWorker:
        return AgentWorker(
            name=name,
            system_prompt=system_prompt,
            tool_names=tool_names,
            model_provider=create_model_provider(settings, settings_store),
            tool_registry=tool_registry,
            output_format=output_format,
        )

    # 研究 Worker：只读（web 搜索 + 知识库 + 文件读取）
    researcher_worker = make_worker(
        name="researcher",
        system_prompt=(
            "你是 DeskBits 的研究员 Worker，负责收集事实与资料。"
            "优先使用 web_search 和知识库；引用来源要标注。"
            "输出简洁的事实性报告，不要建议或客套。"
        ),
        tool_names=[
            "web_search",
            "web_fetch",
            "knowledge_search",
            "search_files",
            "read_file",
        ],
    )
    # 写作 Worker：可产出文档（artifact），基于研究结果写作
    writer_worker = make_worker(
        name="writer",
        system_prompt=(
            "你是 DeskBits 的写作 Worker，负责把研究结果整理成结构清晰的文档。"
            "基于给定的背景上下文写作，不要编造事实；"
            "输出 Markdown 格式的成品。"
        ),
        tool_names=["artifact_draft"],
        output_format="text",
    )
    # 分析 Worker：只读分析，输出结构化 JSON（供下游消费）
    analyst_worker = make_worker(
        name="analyst",
        system_prompt=(
            "你是 DeskBits 的分析师 Worker，负责把信息归纳成结构化结论。"
        ),
        tool_names=["knowledge_search", "read_file", "search_files"],
        output_format="json",
    )

    tool_registry.register(
        DelegatePipelineTool(
            workers={
                "researcher": researcher_worker.as_runner(),
                "writer": writer_worker.as_runner(),
                "analyst": analyst_worker.as_runner(),
            },
            progress_callback=emit_subtask_progress,
        )
    )
    service = AgentService(
        model_provider=model_provider,
        tool_registry=tool_registry,
        trace_store=trace_store,
        intent_router=create_intent_router(
            settings,
            model_provider=model_provider,
            tool_registry=tool_registry,
        ),
        permission_broker=permission_broker,
        memory_store=memory_store,
        memory_user_id=settings.memory_user_id,
        memory_project_id=settings.memory_project_id,
        memory_context_limit=settings.memory_context_limit,
        context_compression_enabled=settings.context_compression_enabled,
        context_compression_threshold=settings.context_compression_threshold,
        auto_memory_candidates=settings.auto_memory_candidates,
        auto_memory_candidates_limit=settings.auto_memory_candidates_limit,
        session_store=session_store,
        attachment_store=attachment_store,
        provider_factory=lambda: create_model_provider(settings, settings_store),
        runtime_events=runtime_bus,
        skill_prompts=_load_enabled_skill_prompts(skill_store, settings_store),
    )
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        reminder_scheduler.start()
        try:
            # ready 信号由 run.py 统一打印（含实际 port/token/data_dir）
            yield
        finally:
            reminder_scheduler.stop()

    app = FastAPI(title="DesktopMate Agent Service", lifespan=lifespan)
    app.state.auth_token = auth_token
    app.state.agent_service = service
    app.state.settings = settings
    app.state.settings_store = settings_store
    app.state.runtime_bus = runtime_bus
    app.state.reminder_service = reminder_service
    app.state.artifact_store = artifact_store
    run_registry = RunRegistry()
    app.state.run_registry = run_registry
    register_error_handlers(app)
    if settings.allowed_origin_list:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.allowed_origin_list,
            allow_credentials=False,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    features = {
        "websocket_chat": True,
        "sessions": True,
        "knowledge": tool_registry.has("knowledge_search"),
        "artifacts": True,
        "voice": True,
        "settings": True,
    }
    app.include_router(create_router(service, features=features))
    app.include_router(create_health_router())
    app.include_router(create_session_router(session_store))
    app.include_router(
        create_projects_router(project_store, session_store, settings.allowed_root_paths)
    )
    app.include_router(create_artifacts_router(artifact_store))
    app.include_router(
        create_settings_router(
            settings_store,
            key_tester=key_tester,
            model_catalog=model_catalog,
            model_tester=model_tester,
            settings=settings,
        )
    )
    app.include_router(create_skills_router(skill_store, settings_store, tool_registry))
    app.include_router(create_runtime_router(runtime_bus, run_registry, permission_broker, reminder_service=reminder_service))
    app.include_router(create_reminders_router(reminder_service))
    app.include_router(create_tasks_router(task_store, task_confirm_tool))
    app.include_router(create_attachment_router(attachment_store))
    app.include_router(create_fs_router(settings.allowed_root_paths))
    app.include_router(create_storage_router(storage_service))
    stt = WhisperSTT(settings=settings)
    app.state.stt = stt
    app.include_router(create_speech_router(stt))
    app.include_router(create_knowledge_router(document_service))
    app.include_router(create_files_router(settings.allowed_root_paths))
    app.include_router(
        create_profile_router(
            service,
            ProfileFactsStore(settings.knowledge_db_path),
            settings_store,
        )
    )
    app.include_router(create_trace_router(trace_store))
    app.include_router(
        create_websocket_router(
            service,
            permission_broker,
            run_registry,
            auth_token=auth_token,
            allowed_origins=settings.allowed_origin_list,
            runtime_bus=runtime_bus,
            reminder_service=reminder_service,
        )
    )
    return app


def _apply_persisted_skills(settings_store: SettingsStore, tool_registry: ToolRegistry) -> None:
    """把上次持久化的技能开关应用到注册表，保证重启后保持用户选择。"""
    for tool in tool_registry.list():
        stored = settings_store.get_skill_enabled(tool.name)
        if stored is not None:
            tool_registry.set_enabled(tool.name, stored)


def _load_enabled_skill_prompts(
    skill_store: SkillPackageStore,
    settings_store: SettingsStore,
) -> list[dict[str, str]]:
    """读取用户启用且是 prompt 形态的技能，供注入系统提示词。

    tool 形态的技能由 create_app 启动时（load_tool_skill）注册为真实工具，
    启停状态通过 _apply_persisted_skills 恢复。
    """
    enabled_names = {
        name
        for name in {package.name for package in skill_store.installed()}
        if settings_store.get_skill_enabled(name) is True
    }
    return [
        {"name": package.name, "prompt": package.effective_prompt}
        for package in skill_store.active_prompt_skills(enabled_names)
    ]


def _create_memory_store(
    settings: Settings,
) -> JsonlMemoryStore | RedisMemoryStore | SqliteMemoryStore:
    if settings.memory_backend == "jsonl":
        return JsonlMemoryStore(settings.memory_dir)
    if settings.memory_backend == "sqlite":
        return SqliteMemoryStore(
            settings.knowledge_db_path,
            events_path=settings.memory_dir / "memory_events.jsonl",
        )
    if settings.memory_backend == "redis":
        return RedisMemoryStore(
            redis_url=settings.redis_url,
            key_prefix=settings.redis_memory_key_prefix,
        )
    raise ValueError(f"Unsupported memory backend: {settings.memory_backend}")


app = create_app()
