"""网页搜索工具。优先 Tavily，失败时降级 DuckDuckGo。"""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, Field, field_validator

from desktopmate_agent.tools.base import BaseTool, RiskLevel, ToolResult


class WebSearchArgs(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    max_results: int = Field(default=5, ge=1, le=10)
    topic: str = Field(default="general", pattern="^(general|news)$")

    @field_validator("topic", mode="before")
    @classmethod
    def normalize_topic(cls, value: object) -> str:
        """Map model labels to Tavily's two supported topic values."""
        normalized = str(value or "general").strip().lower()
        return "news" if normalized == "news" else "general"


class WebSearchTool(BaseTool[WebSearchArgs]):
    name = "web_search"
    description = "Search the public web and return cited results for the current answer."
    args_schema = WebSearchArgs
    risk_level = RiskLevel.READ
    requires_permission = False
    timeout_seconds = 20.0

    def __init__(
        self,
        *,
        api_key: Callable[[], str | None],
        base_url: str = "https://api.tavily.com/search",
    ) -> None:
        self._api_key = api_key
        self.base_url = base_url

    async def run(self, args: WebSearchArgs) -> ToolResult:
        key = self._api_key()
        tavily_error: Exception | None = None
        if not key:
            tavily_error = ValueError("网页搜索尚未配置 Tavily API Key")
        else:
            payload = {
                "api_key": key,
                "query": args.query,
                "topic": args.topic,
                "search_depth": "advanced",
                "max_results": args.max_results,
                "include_answer": False,
                "include_raw_content": False,
            }
            try:
                response = await asyncio.to_thread(_post_json, self.base_url, payload)
                results = _normalize_tavily_results(response, max_results=args.max_results)
                return ToolResult(
                    status="success",
                    summary=f"网页搜索找到 {len(results)} 条结果",
                    data={"query": args.query, "provider": "tavily", "results": results},
                )
            except (OSError, TimeoutError, ValueError) as exc:
                tavily_error = exc
        try:
            results = await asyncio.to_thread(_search_duckduckgo, args.query, args.max_results)
        except (OSError, TimeoutError, ValueError) as exc:
            return ToolResult(
                status="error",
                summary=f"网页搜索失败：Tavily 失败（{tavily_error}）；DuckDuckGo 也失败（{exc}）",
                data={
                    "code": "web_search_failed",
                    "provider": "duckduckgo",
                    "tavily_error": str(tavily_error),
                    "duckduckgo_error": str(exc),
                },
            )
        return ToolResult(
            status="success",
            summary=f"网页搜索找到 {len(results)} 条结果",
            data={"query": args.query, "provider": "duckduckgo", "results": results},
        )


def _normalize_tavily_results(response: dict[str, Any], *, max_results: int) -> list[dict[str, str]]:
    raw_results = response.get("results", []) if isinstance(response, dict) else []
    results: list[dict[str, str]] = []
    for item in raw_results[:max_results] if isinstance(raw_results, list) else []:
        if not isinstance(item, dict) or not item.get("url"):
            continue
        results.append(
            {
                "title": str(item.get("title") or item["url"]),
                "url": str(item["url"]),
                "snippet": str(item.get("content") or "").strip()[:1200],
                "published_at": str(item.get("published_date") or ""),
            }
        )
    return results


def _search_duckduckgo(query: str, max_results: int) -> list[dict[str, str]]:
    try:
        from ddgs import DDGS
    except ImportError as exc:
        raise ValueError("DuckDuckGo 搜索依赖 ddgs 未安装") from exc
    raw_results = DDGS().text(query, max_results=max_results, backend="auto")
    results: list[dict[str, str]] = []
    for item in raw_results:
        if not isinstance(item, dict) or not item.get("href"):
            continue
        results.append(
            {
                "title": str(item.get("title") or item["href"]),
                "url": str(item["href"]),
                "snippet": str(item.get("body") or "").strip()[:1200],
                "published_at": "",
            }
        )
    return results[:max_results]


def test_tavily_api_key(api_key: str, base_url: str = "https://api.tavily.com/search") -> None:
    _post_json(
        base_url,
        {"api_key": api_key, "query": "DeskBits", "max_results": 1, "include_answer": False},
    )


def _post_json(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        raise ValueError(f"Tavily 返回 HTTP {exc.code}: {detail}") from exc
    decoded = json.loads(body)
    if not isinstance(decoded, dict):
        raise ValueError("Tavily 返回格式无效")
    return decoded
