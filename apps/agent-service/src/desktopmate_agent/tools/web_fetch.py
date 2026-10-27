"""Read-only web page fetcher that extracts LLM-ready page text."""

from __future__ import annotations

import asyncio
import html
import ipaddress
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any

from pydantic import BaseModel, Field

from desktopmate_agent.tools.base import BaseTool, RiskLevel, ToolResult


class WebFetchArgs(BaseModel):
    url: str = Field(min_length=1, max_length=2_000)
    max_chars: int = Field(default=12_000, ge=500, le=50_000)
    include_links: bool = True
    max_links: int = Field(default=8, ge=0, le=30)


@dataclass
class FetchResponse:
    url: str
    final_url: str
    status_code: int
    content_type: str
    charset: str
    text: str
    truncated: bool


class WebFetchTool(BaseTool[WebFetchArgs]):
    name = "web_fetch"
    description = "Fetch one public web page and extract title, readable Markdown text, and links."
    args_schema = WebFetchArgs
    risk_level = RiskLevel.READ
    requires_permission = False
    timeout_seconds = 25.0

    def __init__(self, *, max_bytes: int = 2_000_000, timeout_seconds: float = 15.0) -> None:
        self.max_bytes = max_bytes
        self.fetch_timeout_seconds = timeout_seconds

    async def run(self, args: WebFetchArgs) -> ToolResult:
        try:
            fetched = await asyncio.to_thread(
                _fetch_url,
                args.url,
                max_bytes=self.max_bytes,
                timeout_seconds=self.fetch_timeout_seconds,
            )
        except ValueError as exc:
            return ToolResult(
                status="error",
                summary=f"网页抓取失败：{exc}",
                data={"code": "web_fetch_invalid_request", "url": args.url},
            )
        except (OSError, TimeoutError, urllib.error.URLError) as exc:
            return ToolResult(
                status="error",
                summary=f"网页抓取失败：{exc}",
                data={"code": "web_fetch_failed", "url": args.url},
            )

        extraction = _extract_page(fetched.text, fetched.final_url, fetched.content_type)
        content = extraction["content"][: args.max_chars].strip()
        markdown = _to_markdown(
            title=extraction["title"],
            description=extraction["description"],
            content=content,
            links=extraction["links"][: args.max_links] if args.include_links else [],
        )
        title = extraction["title"] or fetched.final_url
        link_count = len(extraction["links"]) if args.include_links else 0
        summary = f"已抓取网页：{title}"
        if fetched.truncated or len(extraction["content"]) > args.max_chars:
            summary += "（内容已截断）"

        return ToolResult(
            status="success",
            summary=summary,
            data={
                "url": fetched.url,
                "final_url": fetched.final_url,
                "title": title,
                "description": extraction["description"],
                "content": content,
                "markdown": markdown,
                "links": extraction["links"][: args.max_links] if args.include_links else [],
                "link_count": link_count,
                "content_type": fetched.content_type,
                "status_code": fetched.status_code,
                "truncated": fetched.truncated or len(extraction["content"]) > args.max_chars,
            },
        )


class _ReadableHtmlParser(HTMLParser):
    _block_tags = {"p", "li", "blockquote", "pre", "h1", "h2", "h3", "h4", "h5", "h6"}
    _skip_tags = {"script", "style", "noscript", "template", "svg", "canvas"}

    def __init__(self, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.title = ""
        self.description = ""
        self.blocks: list[tuple[str, str]] = []
        self.links: list[dict[str, str]] = []
        self._skip_depth = 0
        self._active_tag: str | None = None
        self._active_text: list[str] = []
        self._title_active = False
        self._title_text: list[str] = []
        self._link_href: str | None = None
        self._link_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attrs_map = {name.lower(): value or "" for name, value in attrs}
        if tag in self._skip_tags:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if tag == "title":
            self._title_active = True
            self._title_text = []
            return
        if tag == "meta":
            name = attrs_map.get("name", "").lower()
            prop = attrs_map.get("property", "").lower()
            if name == "description" or prop == "og:description":
                self.description = _clean_text(attrs_map.get("content", ""))[:500]
            return
        if tag == "a" and attrs_map.get("href"):
            self._link_href = urllib.parse.urljoin(self.base_url, attrs_map["href"])
            self._link_text = []
        if tag in self._block_tags:
            self._flush_block()
            self._active_tag = tag
            self._active_text = []
        elif tag == "br":
            self._active_text.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._title_active:
            self._title_text.append(data)
        if self._link_href is not None:
            self._link_text.append(data)
        if self._active_tag:
            self._active_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in self._skip_tags and self._skip_depth:
            self._skip_depth -= 1
            return
        if self._skip_depth:
            return
        if tag == "title" and self._title_active:
            self.title = _clean_text(" ".join(self._title_text))[:200]
            self._title_active = False
            return
        if tag == "a" and self._link_href:
            text = _clean_text(" ".join(self._link_text)) or self._link_href
            if self._link_href.startswith(("http://", "https://")):
                self.links.append({"title": text[:160], "url": self._link_href})
            self._link_href = None
            self._link_text = []
        if self._active_tag == tag:
            self._flush_block()

    def close(self) -> None:
        self._flush_block()
        super().close()

    def _flush_block(self) -> None:
        if not self._active_tag:
            return
        text = _clean_text(" ".join(self._active_text))
        if text and len(text) >= 2:
            self.blocks.append((self._active_tag, text))
        self._active_tag = None
        self._active_text = []


def _fetch_url(url: str, *, max_bytes: int, timeout_seconds: float) -> FetchResponse:
    parsed = urllib.parse.urlparse(url.strip())
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("只支持 http/https URL")
    if not parsed.netloc:
        raise ValueError("URL 缺少域名")
    _assert_public_host(parsed.hostname or "")

    request = urllib.request.Request(
        urllib.parse.urlunparse(parsed),
        headers={
            "User-Agent": "DeskBits/0.1 (+https://deskbits.local)",
            "Accept": "text/html,application/xhtml+xml,text/plain,application/json;q=0.8,*/*;q=0.2",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        content_type = response.headers.get("Content-Type", "")
        charset = response.headers.get_content_charset() or "utf-8"
        raw = response.read(max_bytes + 1)
        truncated = len(raw) > max_bytes
        if truncated:
            raw = raw[:max_bytes]
        text = raw.decode(charset, errors="replace")
        return FetchResponse(
            url=url,
            final_url=response.geturl(),
            status_code=getattr(response, "status", 200),
            content_type=content_type,
            charset=charset,
            text=text,
            truncated=truncated,
        )


def _assert_public_host(hostname: str) -> None:
    normalized = hostname.strip().rstrip(".").lower()
    if normalized in {"localhost", "0", "0.0.0.0"}:
        raise ValueError("不允许抓取本机或内网地址")
    try:
        addresses = [ipaddress.ip_address(normalized)]
    except ValueError:
        try:
            infos = socket.getaddrinfo(normalized, None, type=socket.SOCK_STREAM)
        except socket.gaierror as exc:
            raise ValueError(f"无法解析域名：{hostname}") from exc
        addresses = []
        for info in infos:
            address = info[4][0]
            try:
                addresses.append(ipaddress.ip_address(address))
            except ValueError:
                continue
    if not addresses:
        raise ValueError("无法解析出有效 IP 地址")
    if any(_is_private_address(address) for address in addresses):
        raise ValueError("不允许抓取本机或内网地址")


def _is_private_address(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )


def _extract_page(raw: str, final_url: str, content_type: str) -> dict[str, Any]:
    if "html" not in content_type.lower() and "<html" not in raw[:1000].lower():
        content = _clean_text(raw)
        return {
            "title": urllib.parse.urlparse(final_url).netloc or final_url,
            "description": "",
            "content": content,
            "links": [],
        }
    parser = _ReadableHtmlParser(final_url)
    parser.feed(raw)
    parser.close()
    content = "\n\n".join(_block_to_markdown(tag, text) for tag, text in parser.blocks)
    return {
        "title": parser.title,
        "description": parser.description,
        "content": content.strip(),
        "links": _dedupe_links(parser.links),
    }


def _block_to_markdown(tag: str, text: str) -> str:
    if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
        level = min(max(int(tag[1]), 1), 4)
        return f"{'#' * level} {text}"
    if tag == "li":
        return f"- {text}"
    return text


def _to_markdown(
    *,
    title: str,
    description: str,
    content: str,
    links: list[dict[str, str]],
) -> str:
    parts: list[str] = []
    if title:
        parts.append(f"# {title}")
    if description:
        parts.append(f"> {description}")
    if content:
        parts.append(content)
    if links:
        parts.append("## Links")
        for item in links:
            parts.append(f"- [{item['title']}]({item['url']})")
    return "\n\n".join(parts).strip()


def _dedupe_links(links: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[str] = set()
    deduped: list[dict[str, str]] = []
    for link in links:
        url = link.get("url", "")
        if not url or url in seen:
            continue
        seen.add(url)
        deduped.append(link)
    return deduped


def _clean_text(value: str) -> str:
    unescaped = html.unescape(value)
    collapsed = re.sub(r"\s+", " ", unescaped)
    return collapsed.strip()
