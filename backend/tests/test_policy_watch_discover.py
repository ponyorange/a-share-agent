from app.advisor.policy_watch.discover import extract_article_links, ingest_source


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


def test_seed_does_not_create_article(monkeypatch):
    seen = []
    articles = []

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
