"""联网搜索工具：Tavily / SearXNG / Bing / DuckDuckGo。

智能体在需要实时、外部、知识库之外的信息时调用 web_search。
"""
from __future__ import annotations

import html as html_mod
import json
import logging
import re
from typing import Any

import httpx
from langchain_core.tools import tool as lc_tool

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 15.0
MAX_RESULTS = 8
_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36 PDH-PKG/0.1"
_BROWSER_HEADERS = {
    "User-Agent": _USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate",
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}


def _clean_text(value: Any) -> str:
    text = re.sub(r"<[^>]+>", "", str(value or ""))
    return html_mod.unescape(text).strip()


def _normalize_results(items: list[dict]) -> list[dict]:
    results = []
    for item in items:
        url = str(item.get("url", "") or "").strip()
        title = _clean_text(item.get("title", ""))[:200]
        content = _clean_text(item.get("content") or item.get("snippet") or "")[:500]
        if not url or not title:
            continue
        results.append({
            "title": title,
            "url": url,
            "content": content,
            "score": round(float(item.get("score", 0.0) or 0.0), 3) if item.get("score") is not None else 0.0,
        })
    return results[:MAX_RESULTS]


async def _search_tavily(query: str, top_k: int, api_key: str, base_url: str = "") -> dict:
    if not api_key:
        raise ValueError("Tavily 需要 API Key")
    url = (base_url or "https://api.tavily.com").rstrip("/") + "/search"
    payload = {
        "api_key": api_key,
        "query": query,
        "max_results": top_k,
        "include_answer": True,
        "include_raw_content": False,
    }
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        resp = await client.post(url, json=payload)
        resp.raise_for_status()
        data = resp.json()
    return data


async def _search_searxng(query: str, top_k: int, base_url: str = "") -> dict:
    base = (base_url or "http://localhost:8080").rstrip("/")
    params = {"q": query, "format": "json", "language": "zh-CN", "safesearch": "0"}
    headers = {"User-Agent": _USER_AGENT, "Accept": "application/json"}
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        resp = await client.get(base + "/search", params=params, headers=headers)
        resp.raise_for_status()
        data = resp.json()
    return data


def _parse_bing(html_text: str, top_k: int) -> list[dict]:
    results = []
    for block_match in re.finditer(r'<li class="b_algo".*?</li>', html_text, re.S):
        block = block_match.group(0)
        link_match = re.search(r'<h2[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', block, re.S)
        if not link_match:
            continue
        url = _resolve_bing_url(html_mod.unescape(link_match.group(1)).strip())
        title = _clean_text(link_match.group(2))
        snippet_match = re.search(r'<p[^>]*>(.*?)</p>', block, re.S)
        content = _clean_text(snippet_match.group(1)) if snippet_match else ""
        results.append({"title": title, "url": url, "content": content, "score": 0.0})
        if len(results) >= top_k:
            break
    return results


def _resolve_bing_url(url: str) -> str:
    """把 Bing 的 /ck/a 跳转链接还原成真实地址。"""
    if "bing.com" not in url:
        return url
    match = re.search(r"[?&]u=([^&]+)", url)
    if not match:
        return url
    encoded = match.group(1).strip()
    try:
        import base64
        padded = encoded + "=" * (-len(encoded) % 4)
        decoded = base64.urlsafe_b64decode(padded).decode("utf-8")
        return decoded if decoded.startswith(("http://", "https://")) else url
    except Exception:
        return url


async def _search_bing(query: str, top_k: int) -> dict:
    params = {"q": query, "count": top_k, "setlang": "zh-hans"}
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, follow_redirects=True) as client:
        resp = await client.get("https://www.bing.com/search", params=params, headers=_BROWSER_HEADERS)
        resp.raise_for_status()
        results = _parse_bing(resp.text, top_k)
    return {"results": results}


def _parse_duckduckgo(html_text: str, top_k: int) -> list[dict]:
    results = []
    for link_match in re.finditer(r'<a[^>]*class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', html_text, re.S):
        url = html_mod.unescape(link_match.group(1)).strip()
        title = _clean_text(link_match.group(2))
        tail = html_text[link_match.end():]
        snippet_match = re.search(r'<a[^>]*class="result__snippet"[^>]*>(.*?)</a>', tail, re.S)
        content = _clean_text(snippet_match.group(1)) if snippet_match else ""
        results.append({"title": title, "url": url, "content": content, "score": 0.0})
        if len(results) >= top_k:
            break
    return results


async def _search_duckduckgo(query: str, top_k: int) -> dict:
    headers = dict(_BROWSER_HEADERS)
    headers["Sec-Fetch-Dest"] = "document"
    headers["Sec-Fetch-Mode"] = "navigate"
    headers["Sec-Fetch-Site"] = "none"
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, follow_redirects=True) as client:
        resp = await client.post("https://html.duckduckgo.com/html/", data={"q": query}, headers=headers)
        resp.raise_for_status()
        results = _parse_duckduckgo(resp.text, top_k)
    return {"results": results}


async def perform_web_search(
    query: str,
    top_k: int = 5,
    provider: str = "",
    api_key: str = "",
    base_url: str = "",
) -> dict:
    """执行一次联网搜索，返回统一格式的 JSON 数据。"""
    from app.core.user_settings import get_settings

    settings = get_settings()
    selected = (provider or settings.search_provider or "auto").strip().lower()
    key = api_key or settings.search_api_key
    base = base_url or settings.search_base_url
    top_k = max(1, min(int(top_k or 5), MAX_RESULTS))

    try:
        if selected == "auto":
            if key:
                raw = await _search_tavily(query, top_k, key, base)
            elif base:
                raw = await _search_searxng(query, top_k, base)
            else:
                try:
                    raw = await _search_bing(query, top_k)
                    if not raw.get("results"):
                        raw = await _search_duckduckgo(query, top_k)
                except Exception:
                    raw = await _search_duckduckgo(query, top_k)
        elif selected == "tavily":
            raw = await _search_tavily(query, top_k, key, base)
        elif selected == "searxng":
            raw = await _search_searxng(query, top_k, base)
        elif selected == "bing":
            raw = await _search_bing(query, top_k)
        elif selected == "duckduckgo":
            raw = await _search_duckduckgo(query, top_k)
        else:
            return {"error": f"未知的联网搜索类型: {selected}", "count": 0, "results": [], "is_empty": True}

        results = _normalize_results(raw.get("results", []) or [])
        answer = _clean_text(raw.get("answer", "") or "")[:500]
        return {
            "count": len(results),
            "provider": selected,
            "answer": answer,
            "results": results,
            "is_empty": len(results) == 0,
        }
    except Exception as e:
        logger.warning("Web search failed (%s): %s", selected, e)
        return {"error": f"联网搜索失败: {e}", "count": 0, "results": [], "is_empty": True}


@lc_tool
async def web_search(query: str, top_k: int = 5) -> str:
    """搜索互联网获取最新、实时或知识库之外的信息。

    当用户问实时新闻、最新版本、API 变化、外部网站、需要联网核实的资料时调用此工具。
    不要用于：知识库内已有文档、对话记忆、数学计算、文件统计。
    """
    data = await perform_web_search(query, top_k=top_k)
    return json.dumps(data, ensure_ascii=False)
