"""多轮对话记忆 + RAG 自动触发（用户报告的 15:10/11:26 失忆与漏检回归）。"""

from pathlib import Path

from desktopmate_agent.agent.intent import HeuristicIntentRouter, IntentType
from desktopmate_agent.agent.service import AgentService
from desktopmate_agent.models.provider import FakeModelProvider
from desktopmate_agent.sessions.store import SessionStore
from desktopmate_agent.tools.registry import ToolRegistry
from desktopmate_agent.trace.store import JsonlTraceStore


def make_service(tmp_path: Path, session_store: SessionStore) -> AgentService:
    provider = FakeModelProvider(response_text="收到。")
    service = AgentService(
        model_provider=provider,
        tool_registry=ToolRegistry(),
        trace_store=JsonlTraceStore(tmp_path / "traces"),
        intent_router=HeuristicIntentRouter(),
        session_store=session_store,
    )
    return service


async def test_second_turn_sees_first_turn_in_prompt(tmp_path):
    """同会话第二轮提问时，第一轮的问答必须出现在模型 prompt 中。"""
    session_store = SessionStore(tmp_path / "sessions.db")
    provider = FakeModelProvider(response_text="收到。")
    service = AgentService(
        model_provider=provider,
        tool_registry=ToolRegistry(),
        trace_store=JsonlTraceStore(tmp_path / "traces"),
        intent_router=HeuristicIntentRouter(),
        session_store=session_store,
    )

    # 第一轮：用户报代号
    events = [
        e
        async for e in service.stream_chat(
            text="我的代号是阿尔法", session_id="s-memory"
        )
    ]
    assert any(e.type == "agent_message" for e in events)

    # 第二轮：问代号（避开 question_keywords 触发 RAG，纯测记忆）
    provider.prompts.clear()
    events2 = [
        e
        async for e in service.stream_chat(
            text="我的代号是多少", session_id="s-memory"
        )
    ]
    assert any(e.type == "agent_message" for e in events2)

    assert provider.prompts, "第二轮应调用模型"
    prompt = provider.prompts[-1]
    assert "我的代号是阿尔法" in prompt, "prompt 应包含第一轮用户消息"
    assert "收到。" in prompt, "prompt 应包含第一轮助手回复"
    assert "<history>" in prompt


async def test_history_excludes_current_user_message(tmp_path):
    """历史加载应排除当前这条用户消息（它已被持久化）。"""
    session_store = SessionStore(tmp_path / "sessions.db")
    service = make_service(tmp_path, session_store)

    # 直接构造场景：先有 2 条历史，再补当前消息
    session_store.ensure_session("s-h", mode="chat")
    session_store.add_message("s-h", role="user", text="前一轮问题")
    session_store.add_message("s-h", role="assistant", text="前一轮回答")
    session_store.add_message("s-h", role="user", text="当前问题")

    history = service._load_conversation_history("s-h")
    assert [h["text"] for h in history] == ["前一轮问题", "前一轮回答"]
    assert all(h["text"] != "当前问题" for h in history)


def test_history_includes_latest_structured_plan_for_plan_updates(tmp_path):
    session_store = SessionStore(tmp_path / "sessions.db")
    service = make_service(tmp_path, session_store)
    session_store.ensure_session("s-plan", mode="agent")
    session_store.add_message("s-plan", role="user", text="制定发布计划")
    session_store.add_message(
        "s-plan",
        role="assistant",
        text="已生成计划。",
        metadata={
            "tool_calls": [{"name": "plan_draft"}],
            "tool_results": [
                {
                    "status": "success",
                    "data": {"plan": {"title": "发布计划", "steps": [{"title": "确认范围"}]}},
                }
            ],
        },
    )
    session_store.add_message("s-plan", role="user", text="把计划第二步改成灰度发布")

    history = service._load_conversation_history("s-plan")

    assert history[-1]["role"] == "system"
    assert "当前已制定计划" in history[-1]["text"]
    assert "确认范围" in history[-1]["text"]


def test_only_explicit_personal_knowledge_questions_trigger_search():
    """RAG 只处理明确指向个人资料的提问，避免所有问答都触发检索。"""
    router = HeuristicIntentRouter()

    decision = router.route("量子桂花树是什么")
    assert decision.type == IntentType.DIRECT_RESPONSE

    decision2 = router.route("为什么天空是蓝色的？")
    assert decision2.type == IntentType.DIRECT_RESPONSE

    # 文档关键词路径仍然生效。
    decision3 = router.route("文档里提到的量子桂花树是什么")
    assert decision3.type == IntentType.TOOL_CALL
    assert decision3.tool_name == "knowledge_search"


def test_non_question_stays_direct():
    """非提问型指令不触发知识库检索。"""
    router = HeuristicIntentRouter()
    assert router.route("你好").type == IntentType.DIRECT_RESPONSE
    assert router.route("帮我写一首关于秋天的诗").type == IntentType.DIRECT_RESPONSE
    assert router.route("只回复两个字：你好").type == IntentType.DIRECT_RESPONSE


def test_prompt_build_includes_history_section(tmp_path):
    """_build_prompt 输出应含对话历史段。"""
    from desktopmate_agent.agent.graph import AgentGraph

    graph = AgentGraph(
        model_provider=FakeModelProvider(),
        tool_registry=ToolRegistry(),
        intent_router=HeuristicIntentRouter(),
    )
    prompt = graph._build_prompt(
        user_text="当前问题",
        memory_context=[{"key": "k", "value": "v"}],
        knowledge_context=[],
        history=[{"role": "user", "text": "前一轮"}, {"role": "assistant", "text": "回答"}],
    )
    assert "[System & Rules]" in prompt
    assert "[Current Task]" in prompt
    assert "[Relevant History]" in prompt
    assert "[Output Format]" in prompt
    assert "<history>" in prompt
    assert "- user: 前一轮" in prompt
    assert "- assistant: 回答" in prompt
    assert "[Current Task]\n用户输入:\n当前问题" in prompt


def test_prompt_truncates_history_by_token_budget(tmp_path):
    """历史按粗略 token 预算保留最近轮次。"""
    from desktopmate_agent.agent.graph import AgentGraph

    graph = AgentGraph(
        model_provider=FakeModelProvider(),
        tool_registry=ToolRegistry(),
        intent_router=HeuristicIntentRouter(),
        system_prompt="规则",
    )
    history = []
    for index in range(6):
        history.extend(
            [
                {"role": "user", "text": f"第{index}轮问题 " + "长内容" * 12},
                {"role": "assistant", "text": f"第{index}轮回答 " + "长回复" * 12},
            ]
        )

    prompt = graph._build_prompt(
        user_text="当前问题",
        memory_context=[],
        knowledge_context=[],
        history=history,
        max_context_tokens=80,
        reserve_ratio=0.2,
    )

    assert "（对话历史已按 Token 预算截断，保留最近" in prompt
    assert "第0轮问题" not in prompt
    assert "第5轮问题" in prompt


def test_prompt_omits_old_short_low_relevance_rounds(tmp_path):
    """只过滤 10 轮以前的极短闲聊，最近 10 轮保留。"""
    from desktopmate_agent.agent.graph import AgentGraph

    graph = AgentGraph(
        model_provider=FakeModelProvider(),
        tool_registry=ToolRegistry(),
        intent_router=HeuristicIntentRouter(),
        system_prompt="规则",
    )
    history = [
        {"role": "user", "text": "嗯"},
        {"role": "assistant", "text": "好"},
    ]
    for index in range(10):
        history.extend(
            [
                {"role": "user", "text": f"保留的第{index}轮问题是什么"},
                {"role": "assistant", "text": f"保留的第{index}轮回答"},
            ]
        )

    prompt = graph._build_prompt(
        user_text="当前问题",
        memory_context=[],
        knowledge_context=[],
        history=history,
    )

    assert "（已省略 1 轮简短对话）" in prompt
    assert "- user: 嗯" not in prompt
    assert "保留的第0轮问题是什么" in prompt
