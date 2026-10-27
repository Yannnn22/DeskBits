from datetime import datetime

import pytest

from desktopmate_agent.agent.intent import HeuristicIntentRouter, IntentType, ModelIntentRouter, ReActIntentRouter
from desktopmate_agent.models.provider import FakeModelProvider


def test_router_detects_search_tool_intent_from_natural_language():
    router = HeuristicIntentRouter()

    intent = router.route("帮我找一下 note 文件")

    assert intent.type == IntentType.TOOL_CALL
    assert intent.tool_name == "search_files"
    assert intent.tool_args == {"query": "note", "limit": 10}


def test_router_detects_read_file_command():
    router = HeuristicIntentRouter()

    intent = router.route("/read /tmp/note.md")

    assert intent.type == IntentType.TOOL_CALL
    assert intent.tool_name == "read_file"
    assert intent.tool_args == {"path": "/tmp/note.md"}


def test_router_detects_knowledge_search_command():
    router = HeuristicIntentRouter()

    intent = router.route("/knowledge 怎么配置 Milvus")

    assert intent.type == IntentType.TOOL_CALL
    assert intent.tool_name == "knowledge_search"
    assert intent.tool_args == {"query": "怎么配置 Milvus", "limit": 5}


def test_router_detects_knowledge_refresh_command():
    router = HeuristicIntentRouter()

    intent = router.route("/knowledge-refresh")

    assert intent.type == IntentType.TOOL_CALL
    assert intent.tool_name == "knowledge_refresh"
    assert intent.tool_args == {}


def test_router_detects_draft_write_command():
    router = HeuristicIntentRouter()

    intent = router.route("/draft /tmp/note.md\n# Draft")

    assert intent.type == IntentType.TOOL_CALL
    assert intent.tool_name == "draft_write"
    assert intent.tool_args == {"path": "/tmp/note.md", "content": "# Draft"}


def test_router_refuses_unsupported_destructive_commands():
    router = HeuristicIntentRouter()

    intent = router.route("删除整个桌面")

    assert intent.type == IntentType.REFUSAL
    assert intent.reason == "当前不支持删除、移动或批量修改文件。"


def test_router_keeps_normal_chat_as_direct_response():
    router = HeuristicIntentRouter()

    intent = router.route("你好")

    assert intent.type == IntentType.DIRECT_RESPONSE


def test_router_creates_one_time_reminder_from_natural_language():
    router = HeuristicIntentRouter()

    intent = router.route("提醒我明天12点之前学完第一章")

    assert intent.type == IntentType.TOOL_CALL
    assert intent.tool_name == "task_draft"
    assert intent.tool_args["title"] == "学完第一章"
    assert intent.tool_args["timezone"] == "Asia/Shanghai"
    assert intent.tool_args["create_reminder"] is True
    due_at = datetime.fromisoformat(intent.tool_args["due_at"])
    assert due_at.hour == 12
    assert due_at.minute == 0


def test_router_parses_chinese_numerals_and_periods():
    """中文数字小时/分钟 + 下午/晚上/中午 时段换算。"""
    router = HeuristicIntentRouter()

    cases = [
        ("提醒我明天下午一点吃药", "吃药", 13, 0),
        ("提醒我明天晚上十一点半睡觉", "睡觉", 23, 30),
        ("提醒我明天中午一点开个会", "开个会", 13, 0),
        ("提醒我后天上午9点开会", "开会", 9, 0),
        ("提醒我明天下午三点二十出门", "出门", 15, 20),
    ]
    for text, expected_title, expected_hour, expected_minute in cases:
        intent = router.route(text)
        assert intent.type == IntentType.TOOL_CALL, text
        assert intent.tool_name == "task_draft", text
        assert intent.tool_args["title"] == expected_title, text
        due_at = datetime.fromisoformat(intent.tool_args["due_at"])
        assert due_at.hour == expected_hour, text
        assert due_at.minute == expected_minute, text


def test_router_requires_date_and_time_for_reminder():
    router = HeuristicIntentRouter()
    # 只有事项没有时间 → 拒绝并提示补时间
    intent = router.route("提醒我学完第一章")
    assert intent.type == IntentType.REFUSAL


def test_router_creates_task_from_natural_language():
    router = HeuristicIntentRouter()

    intent = router.route("添加待办：整理第一章笔记")

    assert intent.type == IntentType.TOOL_CALL
    assert intent.tool_name == "task_draft"
    assert intent.tool_args["title"] == "整理第一章笔记"
    assert intent.tool_args["create_reminder"] is False


def test_router_creates_candidate_document_from_explicit_writing_command():
    router = HeuristicIntentRouter()

    intent = router.route("写一篇项目周报，包含进展、风险和下周计划", mode="agent")

    assert intent.type == IntentType.TOOL_CALL
    assert intent.tool_name == "artifact_draft"
    assert intent.tool_args["title"].startswith("项目周报")


def test_chat_answers_writing_request_without_creating_artifact():
    router = HeuristicIntentRouter()

    intent = router.route("帮我完成一份关于电商平台分析的调研文档", mode="chat")

    assert intent.type == IntentType.DIRECT_RESPONSE
    assert intent.tool_name is None


@pytest.mark.asyncio
async def test_react_router_uses_model_to_enrich_task_candidate():
    router = ReActIntentRouter(
        model_provider=FakeModelProvider(
            response_text=(
                '{"title":"按时服用药物","description":"午餐后服用当天药物，并留意身体状况。",'
                '"category":"健康","priority":"high","next_action":"把药放到午餐位置。",'
                '"reminder_message":"午餐后该服药了。","memory_summary":"已安排明天下午一点服药。"}'
            )
        )
    )

    intent = await router.route("提醒我明天下午一点吃药")

    assert intent.tool_name == "task_draft"
    assert intent.tool_args["title"] == "按时服用药物"
    assert intent.tool_args["category"] == "健康"
    assert intent.tool_args["priority"] == "high"
    assert intent.tool_args["create_reminder"] is True
    assert intent.tool_args["memory_summary"] == "已安排明天下午一点服药。"


@pytest.mark.asyncio
async def test_react_router_keeps_knowledge_anchor_when_model_guesses_file_tool():
    router = ReActIntentRouter(
        model_provider=FakeModelProvider(
            response_text=(
                '{"type":"tool_call","tool_name":"read_file",'
                '"tool_args":{"path":"test.md"},"confidence":0.95}'
            )
        )
    )

    intent = await router.route("我的知识库里 test.md 提到了什么？")

    assert intent.type == IntentType.TOOL_CALL
    assert intent.tool_name == "knowledge_search"


@pytest.mark.asyncio
async def test_model_router_parses_structured_tool_call():
    provider = FakeModelProvider(
        response_text=(
            '{"type":"tool_call","tool_name":"search_files",'
            '"tool_args":{"query":"note","limit":10},"confidence":0.91}'
        )
    )
    router = ModelIntentRouter(model_provider=provider)

    intent = await router.route("帮我找一下 note 文件")

    assert intent.type == IntentType.TOOL_CALL
    assert intent.tool_name == "search_files"
    assert intent.tool_args == {"query": "note", "limit": 10}
    assert intent.confidence == 0.91


@pytest.mark.asyncio
async def test_model_router_falls_back_when_model_returns_invalid_json():
    provider = FakeModelProvider(response_text="我觉得应该搜索一下。")
    router = ModelIntentRouter(model_provider=provider)

    intent = await router.route("帮我找一下 note 文件")

    assert intent.type == IntentType.DIRECT_RESPONSE
    assert intent.confidence == 0.0
    assert intent.reason == "模型没有返回有效的意图 JSON。"


def test_model_router_prompt_guides_web_search_for_realtime_questions():
    """用户没说要联网、但问题需要实时信息时，prompt 应引导模型选 web_search。"""
    router = ModelIntentRouter(model_provider=FakeModelProvider(""))

    prompt = router._build_prompt("2026 年最新的 AI 大模型排名是什么")

    assert "web_search" in prompt
    assert "实时" in prompt or "最新信息" in prompt


def test_deep_research_triggers_pipeline() -> None:
    """深度调研请求 → delegate_pipeline（研究+写作两阶段，顺序执行）。"""
    from desktopmate_agent.agent.intent import HeuristicIntentRouter

    router = HeuristicIntentRouter()
    decision = router.route("深度调研一下 Agent 开发框架", mode="agent")
    assert decision.tool_name == "delegate_pipeline"
    stages = decision.tool_args["stages"]
    assert stages[0]["worker"] == "researcher"
    assert stages[1]["worker"] == "writer"
    assert "Agent 开发框架" in decision.tool_args["goal"] or any(
        "Agent 开发框架" in stage["task"] for stage in stages
    )


def test_deep_research_does_not_shadow_parallel() -> None:
    """「并行查」仍是 delegate_orchestrate，不误触 pipeline。"""
    from desktopmate_agent.agent.intent import HeuristicIntentRouter

    router = HeuristicIntentRouter()
    decision = router.route("帮我并行查三件事", mode="agent")
    assert decision.tool_name == "delegate_orchestrate"


def test_research_hint_extracts_topic() -> None:
    from desktopmate_agent.agent.intent import HeuristicIntentRouter

    router = HeuristicIntentRouter()
    topic = router._extract_topic("深度调研一下 多Agent协作模式")
    assert "多Agent协作模式" in topic
