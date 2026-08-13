from datetime import datetime
from zoneinfo import ZoneInfo

from app.advisor.policy_watch.fanout import fanout_user
from app.advisor.policy_watch.mailer import build_policy_watch_email

SH = ZoneInfo("Asia/Shanghai")

_ARTICLE = {
    "id": "a1",
    "url_key": "https://www.gov.cn/a",
    "url": "https://www.gov.cn/a.htm",
    "title": "国务院印发新能源指导意见",
    "source_key": "gov_zhengce",
    "source_label": "中国政府网 · 最新政策",
    "body_ok": True,
    "interpret_status": "ready",
    "interpretation": {
        "impact_score": 0.8,
        "direction": "up",
        "summary": "新能源政策利好产业链",
        "sectors": [{"name": "新能源", "reason": "补贴"}],
        "symbols": [
            {
                "symbol": "300750",
                "name": "宁德时代",
                "reason": "电池",
                "verified": True,
            }
        ],
        "category": "policy",
    },
}


def test_build_email_single_and_digest():
    subject, body = build_policy_watch_email(
        [
            {
                "title": "国务院印发新能源指导意见",
                "source_label": "中国政府网 · 最新政策",
                "url": "https://www.gov.cn/a.htm",
                "summary": "新能源政策利好产业链",
                "direction": "up",
                "sectors": [{"name": "新能源", "reason": "补贴"}],
                "symbols": [{"symbol": "300750", "name": "宁德时代", "verified": True}],
                "body_ok": True,
            }
        ]
    )
    assert subject.startswith("[政策雷达]")
    assert "利好" in subject
    assert "https://www.gov.cn/a.htm" in body
    assert "研究参考，不构成投资建议" in body
    sub2, _ = build_policy_watch_email(
        [
            {"title": "A", "source_label": "s", "url": "u1", "summary": "a", "direction": "up", "sectors": [], "symbols": [], "body_ok": True},
            {"title": "B", "source_label": "s", "url": "u2", "summary": "b", "direction": "down", "sectors": [], "symbols": [], "body_ok": True},
        ]
    )
    assert "2条" in sub2


def _base_settings(**extra):
    body = {
        "user_id": "u1",
        "enabled": True,
        "sensitivity": "medium",
        "scan_mode": "always",
        "interval_trading_min": 15,
        "interval_offhours_min": 60,
        "preset_ids": ["gov_zhengce"],
        "custom_sources": [],
        "notify_email": "a@b.c",
        "last_fanout_at": None,
    }
    body.update(extra)
    return body


def test_fanout_sends_once(monkeypatch):
    sent = []
    items = []
    unfanned = [_ARTICLE]

    monkeypatch.setattr(
        "app.advisor.policy_watch.fanout.list_unfanned_articles",
        lambda uid, keys: list(unfanned),
    )
    monkeypatch.setattr(
        "app.advisor.policy_watch.fanout.insert_item",
        lambda uid, aid, status, notified_at=None: items.append(status) or {"id": "i1"},
    )
    monkeypatch.setattr(
        "app.advisor.policy_watch.fanout.send_email",
        lambda to, subject, body: sent.append((to, subject, body)),
    )
    monkeypatch.setattr("app.advisor.policy_watch.fanout.touch_settings", lambda *a, **k: None)
    monkeypatch.setattr("app.advisor.policy_watch.fanout.recent_notified_titles", lambda *a, **k: [])
    monkeypatch.setattr("app.advisor.policy_watch.fanout.peek_verified_email", lambda uid: "a@b.c")
    monkeypatch.setattr(
        "app.advisor.policy_watch.fanout.in_user_scan_window", lambda s, now=None: True
    )
    monkeypatch.setattr(
        "app.advisor.policy_watch.fanout.user_interval_elapsed", lambda s, now=None: True
    )

    out = fanout_user(_base_settings())
    assert out["emailed"] == 1
    assert sent[0][0] == "a@b.c"
    assert items == ["sent"]

    unfanned.clear()
    out2 = fanout_user(_base_settings())
    assert out2["emailed"] == 0
    assert len(sent) == 1


def test_fanout_skips_low_sensitivity(monkeypatch):
    items = []
    article = dict(_ARTICLE)
    article["interpretation"] = {
        "impact_score": 0.5,
        "direction": "up",
        "summary": "新闻",
        "sectors": [],
        "symbols": [],
        "category": "news",
    }
    monkeypatch.setattr(
        "app.advisor.policy_watch.fanout.list_unfanned_articles",
        lambda uid, keys: [article],
    )
    monkeypatch.setattr(
        "app.advisor.policy_watch.fanout.insert_item",
        lambda uid, aid, status, notified_at=None: items.append(status) or {"id": "i1"},
    )
    monkeypatch.setattr("app.advisor.policy_watch.fanout.send_email", lambda *a, **k: None)
    monkeypatch.setattr("app.advisor.policy_watch.fanout.touch_settings", lambda *a, **k: None)
    monkeypatch.setattr("app.advisor.policy_watch.fanout.recent_notified_titles", lambda *a, **k: [])
    monkeypatch.setattr(
        "app.advisor.policy_watch.fanout.in_user_scan_window", lambda s, now=None: True
    )
    monkeypatch.setattr(
        "app.advisor.policy_watch.fanout.user_interval_elapsed", lambda s, now=None: True
    )
    out = fanout_user(_base_settings(sensitivity="low"))
    assert out["emailed"] == 0
    assert items == ["skipped"]


def test_fanout_trading_only_weekend(monkeypatch):
    inserted = []
    monkeypatch.setattr(
        "app.advisor.policy_watch.fanout.list_unfanned_articles",
        lambda *a, **k: inserted.append("listed") or [_ARTICLE],
    )
    monkeypatch.setattr(
        "app.advisor.policy_watch.fanout.in_user_scan_window", lambda s, now=None: False
    )
    monkeypatch.setattr(
        "app.advisor.policy_watch.fanout.user_interval_elapsed", lambda s, now=None: True
    )
    out = fanout_user(
        _base_settings(scan_mode="trading_only"),
        now=datetime(2026, 8, 15, 10, 0, tzinfo=SH),
    )
    assert out["skipped"] == 1
    assert inserted == []


def test_fanout_smtp_fail_no_retry(monkeypatch):
    items = []
    unfanned = [_ARTICLE]

    def _insert(uid, aid, status, notified_at=None):
        items.append(status)
        if status in {"sent", "failed"}:
            unfanned.clear()
        return {"id": "i1"}

    monkeypatch.setattr(
        "app.advisor.policy_watch.fanout.list_unfanned_articles",
        lambda uid, keys: list(unfanned),
    )
    monkeypatch.setattr("app.advisor.policy_watch.fanout.insert_item", _insert)
    monkeypatch.setattr(
        "app.advisor.policy_watch.fanout.send_email",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("smtp")),
    )
    monkeypatch.setattr("app.advisor.policy_watch.fanout.touch_settings", lambda *a, **k: None)
    monkeypatch.setattr("app.advisor.policy_watch.fanout.recent_notified_titles", lambda *a, **k: [])
    monkeypatch.setattr("app.advisor.policy_watch.fanout.peek_verified_email", lambda uid: "a@b.c")
    monkeypatch.setattr(
        "app.advisor.policy_watch.fanout.in_user_scan_window", lambda s, now=None: True
    )
    monkeypatch.setattr(
        "app.advisor.policy_watch.fanout.user_interval_elapsed", lambda s, now=None: True
    )
    out = fanout_user(_base_settings())
    assert items == ["failed"]
    out2 = fanout_user(_base_settings())
    assert out2["emailed"] == 0
    assert items == ["failed"]
