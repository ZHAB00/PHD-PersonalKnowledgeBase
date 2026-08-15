"""联网搜索工具单元测试。"""
import json

import pytest

from app.rag import web_search as ws


SAMPLE_BING_HTML = """
<ol id="b_results">
  <li class="b_algo">
    <h2><a href="https://example.com/a">Example <b>Alpha</b></a></h2>
    <p>这是第一条结果的摘要。</p>
  </li>
  <li class="b_algo">
    <h2><a href="https://example.com/b">Example Beta</a></h2>
    <p>这是第二条结果的摘要。</p>
  </li>
</ol>
"""

SAMPLE_DDG_HTML = """
<div class="result">
  <a class="result__a" href="https://example.com/one">Duck One</a>
  <a class="result__snippet" href="https://example.com/one">第一条片段</a>
</div>
<div class="result">
  <a class="result__a" href="https://example.com/two">Duck Two</a>
  <a class="result__snippet" href="https://example.com/two">第二条片段</a>
</div>
"""


def test_normalize_results_filters_and_limits():
    items = []
    for i in range(10):
        items.append({
            "title": f"<b>标题 {i}</b>",
            "url": f"https://example.com/{i}",
            "content": f"<p>内容 {i}</p>",
            "score": i,
        })
    items.append({"title": "无链接", "content": "不应出现"})
    results = ws._normalize_results(items)
    assert len(results) == ws.MAX_RESULTS
    assert results[0]["title"] == "标题 0"
    assert results[0]["content"] == "内容 0"
    assert all(r["url"] for r in results)


def test_parse_bing_extracts_results():
    results = ws._parse_bing(SAMPLE_BING_HTML, 5)
    assert len(results) == 2
    assert results[0]["title"] == "Example Alpha"
    assert results[0]["url"] == "https://example.com/a"
    assert results[0]["content"] == "这是第一条结果的摘要。"


def test_parse_duckduckgo_extracts_results():
    results = ws._parse_duckduckgo(SAMPLE_DDG_HTML, 5)
    assert len(results) == 2
    assert results[0]["title"] == "Duck One"
    assert results[1]["content"] == "第二条片段"


def test_resolve_bing_url_decodes_redirect():
    real = "https://example.com/path?q=a+b&x=1"
    encoded = "aHR0cHM6Ly9leGFtcGxlLmNvbS9wYXRoP3E9YStiJng9MQ"
    redirect = f"https://www.bing.com/ck/a?a=b&u={encoded}&ntb=1"
    assert ws._resolve_bing_url(redirect) == real


def test_resolve_bing_url_keeps_normal_url():
    assert ws._resolve_bing_url("https://example.com/a") == "https://example.com/a"
    assert ws._resolve_bing_url("https://www.bing.com/search?q=test") == "https://www.bing.com/search?q=test"


def test_parse_bing_decodes_redirect_url():
    encoded = "aHR0cHM6Ly9leGFtcGxlLmNvbS9yZWFs"
    html_text = f"""
    <li class="b_algo">
      <h2><a href="https://www.bing.com/ck/a?a=b&u={encoded}">Real Page</a></h2>
      <p>真实摘要</p>
    </li>
    """
    results = ws._parse_bing(html_text, 5)
    assert results[0]["url"] == "https://example.com/real"


@pytest.mark.asyncio
async def test_perform_web_search_tavily_unified(monkeypatch):
    async def fake_tavily(query, top_k, api_key, base_url=""):
        return {
            "answer": "综合回答",
            "results": [
                {"title": "结果一", "url": "https://example.com/1", "content": "片段一"},
            ],
        }

    monkeypatch.setattr(ws, "_search_tavily", fake_tavily)
    data = await ws.perform_web_search("测试", top_k=3, provider="tavily", api_key="key")
    assert data["count"] == 1
    assert data["provider"] == "tavily"
    assert data["answer"] == "综合回答"
    assert data["results"][0]["url"] == "https://example.com/1"
    assert data["is_empty"] is False


@pytest.mark.asyncio
async def test_perform_web_search_auto_fallback(monkeypatch):
    async def fail_bing(query, top_k):
        raise RuntimeError("bing down")

    async def fake_ddg(query, top_k):
        return {"results": [{"title": "备用结果", "url": "https://example.com/fallback", "content": "来自 DuckDuckGo"}]}

    monkeypatch.setattr(ws, "_search_bing", fail_bing)
    monkeypatch.setattr(ws, "_search_duckduckgo", fake_ddg)
    data = await ws.perform_web_search("测试", top_k=5, provider="auto", api_key="", base_url="")
    assert data["count"] == 1
    assert data["results"][0]["title"] == "备用结果"


@pytest.mark.asyncio
async def test_perform_web_search_unknown_provider():
    data = await ws.perform_web_search("测试", provider="not-exist")
    assert data["is_empty"] is True
    assert "未知" in data["error"]


@pytest.mark.asyncio
async def test_web_search_tool_returns_json(monkeypatch):
    async def fake_perform(query, top_k=5, provider="", api_key="", base_url=""):
        return {"count": 0, "provider": "auto", "answer": "", "results": [], "is_empty": True}

    monkeypatch.setattr(ws, "perform_web_search", fake_perform)
    result = await ws.web_search.ainvoke({"query": "测试"})
    assert json.loads(result)["is_empty"] is True
