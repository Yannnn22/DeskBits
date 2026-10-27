from desktopmate_agent.agent.graph import AgentGraph
from desktopmate_agent.agent.intent import HeuristicIntentRouter
from desktopmate_agent.models.provider import FakeModelProvider
from desktopmate_agent.tools.registry import ToolRegistry
from desktopmate_agent.tools.web_search import WebSearchTool


def test_web_search_normalizes_unsupported_topic_to_general():
    args = WebSearchTool(api_key=lambda: "test-key").parse_args(
        {"query": "主流 Agent 开发框架", "topic": "technology"}
    )

    assert args.topic == "general"


def test_web_search_tool_returns_structured_results(monkeypatch):
    import desktopmate_agent.tools.web_search as module

    monkeypatch.setattr(
        module,
        "_post_json",
        lambda *_args, **_kwargs: {
            "results": [
                {
                    "title": "DeskBits",
                    "url": "https://example.com/deskbits",
                    "content": "A local desktop agent.",
                    "published_date": "2026-08-09",
                }
            ]
        },
    )
    tool = WebSearchTool(api_key=lambda: "test-key")
    result = __import__("asyncio").run(tool.run(tool.parse_args({"query": "DeskBits"})))

    assert result.status == "success"
    assert result.data["results"][0]["title"] == "DeskBits"
    assert result.data["results"][0]["url"] == "https://example.com/deskbits"


def test_web_search_falls_back_to_ddgs_without_tavily_key(monkeypatch):
    import desktopmate_agent.tools.web_search as module

    monkeypatch.setattr(
        module,
        "_search_duckduckgo",
        lambda query, max_results: [
            {
                "title": f"Duck result for {query}",
                "url": "https://example.com/ddg",
                "snippet": "DuckDuckGo fallback result.",
                "published_at": "",
            }
        ][:max_results],
    )
    tool = WebSearchTool(api_key=lambda: None)
    result = __import__("asyncio").run(tool.run(tool.parse_args({"query": "DeskBits"})))

    assert result.status == "success"
    assert result.data["provider"] == "duckduckgo"
    assert result.data["results"][0]["url"] == "https://example.com/ddg"


def test_web_search_falls_back_to_ddgs_when_tavily_fails(monkeypatch):
    import desktopmate_agent.tools.web_search as module

    def fail_tavily(*_args, **_kwargs):
        raise TimeoutError("timeout")

    monkeypatch.setattr(module, "_post_json", fail_tavily)
    monkeypatch.setattr(
        module,
        "_search_duckduckgo",
        lambda _query, _max_results: [
            {
                "title": "Fallback",
                "url": "https://example.com/fallback",
                "snippet": "Fallback body.",
                "published_at": "",
            }
        ],
    )
    tool = WebSearchTool(api_key=lambda: "test-key")
    result = __import__("asyncio").run(tool.run(tool.parse_args({"query": "DeskBits"})))

    assert result.status == "success"
    assert result.data["provider"] == "duckduckgo"
    assert result.data["results"][0]["title"] == "Fallback"


def test_web_search_results_become_web_citations():
    graph = AgentGraph(
        model_provider=FakeModelProvider("x"),
        tool_registry=ToolRegistry(),
        intent_router=HeuristicIntentRouter(),
    )
    _, citations = graph._knowledge_from_tool_results(
        [
            {
                "name": "web_search",
                "status": "success",
                "data": {
                    "results": [
                        {
                            "title": "DeskBits",
                            "url": "https://example.com/deskbits",
                            "snippet": "A local desktop agent.",
                        }
                    ]
                },
            }
        ]
    )

    assert citations == [
        {
            "title": "DeskBits",
            "source_path": "https://example.com/deskbits",
            "source_type": "web",
            "url": "https://example.com/deskbits",
            "chunk_id": "",
        }
    ]


def test_heuristic_web_command_routes_to_web_search():
    decision = HeuristicIntentRouter().route("/web DeskBits agent")
    assert decision.tool_name == "web_search"
    assert decision.tool_args["query"] == "DeskBits agent"


def test_web_results_not_labeled_as_subtasks():
    """B1 修复：web 搜索结果的 snippet 不应被误标成"子任务 N"。"""
    graph = AgentGraph(
        model_provider=FakeModelProvider("x"),
        tool_registry=ToolRegistry(),
        intent_router=HeuristicIntentRouter(),
    )
    knowledge, citations = graph._knowledge_from_tool_results(
        [
            {
                "name": "web_search",
                "status": "success",
                "data": {
                    "results": [
                        {
                            "title": "DeskBits",
                            "url": "https://example.com/deskbits",
                            "snippet": "A local desktop agent.",
                        }
                    ]
                },
            }
        ]
    )
    # 知识上下文：web 结果用原标题，不出现"子任务"标签
    assert len(knowledge) == 1
    assert knowledge[0]["title"] == "DeskBits"
    assert "子任务" not in knowledge[0]["title"]
    # citations 正常
    assert len(citations) == 1
    assert citations[0]["source_type"] == "web"


def test_delegate_results_still_labeled_as_subtasks():
    """B1 修复：delegate 结果（含 goal/text、无 url）仍标"子任务"。"""
    graph = AgentGraph(
        model_provider=FakeModelProvider("x"),
        tool_registry=ToolRegistry(),
        intent_router=HeuristicIntentRouter(),
    )
    knowledge, _ = graph._knowledge_from_tool_results(
        [
            {
                "name": "delegate_orchestrate",
                "status": "success",
                "data": {
                    "results": [
                        {"goal": "查知识库", "status": "success", "text": "找到了 test.md"},
                    ]
                },
            }
        ]
    )
    assert len(knowledge) == 1
    assert knowledge[0]["title"] == "子任务：查知识库"
    assert knowledge[0]["content"] == "找到了 test.md"
