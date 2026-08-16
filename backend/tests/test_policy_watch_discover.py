from app.advisor.agent.web_fetch import html_to_text
from app.advisor.policy_watch.discover import (
    extract_article_links,
    fetch_list_html,
    ingest_source,
)


HTML = """
<html><body>
<a href="/zhengce/content/2026-08/13/c_1.htm">国务院印发指导意见</a>
<a href="/home">首页</a>
<a href="https://evil.com/x">外站</a>
</body></html>
"""


def test_extract_same_host_article_links():
    links = extract_article_links(HTML, "https://www.gov.cn/zhengce/zuixin/", max_links=20)
    assert len(links) == 1
    assert links[0]["url"].startswith("https://www.gov.cn/zhengce/content/")
    assert "指导意见" in links[0]["title"]


def test_stripped_text_cannot_extract_links():
    text = html_to_text(HTML, max_chars=8000)
    assert extract_article_links(text, "https://www.gov.cn/zhengce/zuixin/") == []


AJAX_HTML = """
<html><body>
<ul id="list-1-ajax-id"></ul>
<script>
$.ajax({ url: "./ZUIXINZHENGCE.json" });
</script>
<a href="/home/2023-03/29/content_1.htm">国务院部门网站</a>
</body></html>
"""

FEED_JSON = """
[
  {"TITLE": "国务院印发指导意见", "URL": "https://www.gov.cn/zhengce/content/2026-08/13/c_1.htm"},
  {"TITLE": "另一篇", "URL": "https://www.gov.cn/zhengce/content/2026-08/12/c_2.htm"}
]
"""


def test_ajax_json_feed_preferred_over_nav_links(monkeypatch):
    from app.advisor.policy_watch.discover import _load_links

    monkeypatch.setattr(
        "app.advisor.policy_watch.discover.policy_watch_config",
        lambda: {"presets": {"gov_zhengce": {"list_url": "https://www.gov.cn/zhengce/zuixin/"}}},
    )
    monkeypatch.setattr(
        "app.advisor.policy_watch.discover.fetch_list_html",
        lambda url: AJAX_HTML if url.endswith("/") else "",
    )
    monkeypatch.setattr(
        "app.advisor.policy_watch.discover.fetch_url_html",
        lambda url: FEED_JSON if url.endswith(".json") else "错误：不应请求",
    )
    links = _load_links(
        {
            "kind": "preset",
            "preset_id": "gov_zhengce",
            "url": "https://www.gov.cn/zhengce/zuixin/",
        },
        max_links=20,
    )
    assert [x["title"] for x in links] == ["国务院印发指导意见", "另一篇"]
    assert links[0]["url"].endswith("c_1.htm")


def test_load_links_uses_explicit_feed_url(monkeypatch):
    from app.advisor.policy_watch.discover import _load_links

    calls: list[str] = []

    def fake_html(url: str) -> str:
        calls.append(url)
        if url.endswith(".json"):
            return FEED_JSON
        return "<html><body>拦截页</body></html>"

    monkeypatch.setattr(
        "app.advisor.policy_watch.discover.fetch_url_html",
        fake_html,
    )
    monkeypatch.setattr(
        "app.advisor.policy_watch.discover.fetch_list_html",
        lambda _url: (_ for _ in ()).throw(AssertionError("不应抓栏目 HTML")),
    )
    links = _load_links(
        {
            "kind": "preset",
            "preset_id": "gov_zhengce",
            "url": "https://www.gov.cn/zhengce/zuixin/",
            "feed_url": "https://www.gov.cn/zhengce/zuixin/ZUIXINZHENGCE.json",
        },
        max_links=20,
    )
    assert [x["title"] for x in links] == ["国务院印发指导意见", "另一篇"]
    assert calls == ["https://www.gov.cn/zhengce/zuixin/ZUIXINZHENGCE.json"]


def test_fetch_list_html_keeps_anchors(monkeypatch):
    monkeypatch.setattr(
        "app.advisor.policy_watch.discover.fetch_url_html",
        lambda _url: HTML,
    )
    body = fetch_list_html("https://www.gov.cn/zhengce/zuixin/")
    links = extract_article_links(body, "https://www.gov.cn/zhengce/zuixin/")
    assert len(links) == 1
    assert links[0]["url"].startswith("https://www.gov.cn/zhengce/content/")


def test_seed_does_not_create_article(monkeypatch):
    seen = []
    articles = []

    monkeypatch.setattr(
        "app.advisor.policy_watch.discover.policy_watch_config",
        lambda: {"max_fetch_per_tick": 5, "max_list_links": 20, "presets": {}},
    )
    monkeypatch.setattr(
        "app.advisor.policy_watch.discover.fetch_list_html",
        lambda _url: HTML,
    )
    monkeypatch.setattr(
        "app.advisor.policy_watch.discover.seed_seen",
        lambda sk, links, now=None: seen.append(links) or len(links),
    )
    monkeypatch.setattr(
        "app.advisor.policy_watch.discover.upsert_article",
        lambda **kw: articles.append(kw) or {"id": "a1"},
    )
    monkeypatch.setattr(
        "app.advisor.policy_watch.discover.touch_source_scan", lambda *a, **k: None
    )
    monkeypatch.setattr(
        "app.advisor.policy_watch.discover.clear_seeding", lambda *a, **k: None
    )
    out = ingest_source(
        {
            "source_key": "gov_zhengce",
            "kind": "preset",
            "preset_id": "gov_zhengce",
            "url": "https://www.gov.cn/zhengce/zuixin/",
            "label": "中国政府网 · 最新政策",
            "seeding": True,
        }
    )
    assert out["seeded"] >= 1
    assert articles == []


def test_new_link_upserts_article(monkeypatch):
    articles = []
    monkeypatch.setattr(
        "app.advisor.policy_watch.discover.fetch_list_html",
        lambda _url: HTML,
    )
    monkeypatch.setattr(
        "app.advisor.policy_watch.discover.mark_seen",
        lambda *a, **k: True,
    )
    monkeypatch.setattr(
        "app.advisor.policy_watch.discover.fetch_url_with_escalation",
        lambda url: "这是政策正文内容，足够长用于测试解读。" * 3,
    )
    monkeypatch.setattr(
        "app.advisor.policy_watch.discover.upsert_article",
        lambda **kw: articles.append(kw) or {"id": "a1"},
    )
    monkeypatch.setattr(
        "app.advisor.policy_watch.discover.touch_source_scan", lambda *a, **k: None
    )
    monkeypatch.setattr(
        "app.advisor.policy_watch.discover.policy_watch_config",
        lambda: {"max_fetch_per_tick": 5, "max_list_links": 20, "presets": {}},
    )
    out = ingest_source(
        {
            "source_key": "gov_zhengce",
            "kind": "preset",
            "preset_id": "gov_zhengce",
            "url": "https://www.gov.cn/zhengce/zuixin/",
            "label": "中国政府网 · 最新政策",
            "seeding": False,
        }
    )
    assert out["new_articles"] == 1
    assert articles[0]["body_ok"] is True
