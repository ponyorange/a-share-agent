from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.advisor.policy_watch.urls import (
    article_open_url,
    normalize_title,
    normalize_url_key,
    titles_similar,
)
from app.advisor.policy_watch.schedule import (
    clamp_interval,
    current_interval_minutes,
    in_user_scan_window,
    user_interval_elapsed,
)
from app.advisor.policy_watch.sensitivity import direction_label, should_email

SH = ZoneInfo("Asia/Shanghai")


def test_article_open_url_only_http():
    assert (
        article_open_url("https://www.gov.cn/zhengce/content/2026-08/13/x.htm")
        == "https://www.gov.cn/zhengce/content/2026-08/13/x.htm"
    )
    assert article_open_url("policy://cctv/20260813/title") is None
    assert article_open_url("javascript:alert(1)") is None
    assert article_open_url("") is None


def test_normalize_url_key_strips_tracking():
    a = normalize_url_key("https://www.gov.cn/zhengce/content/2026-08/13/x.htm?utm_source=a&from=b")
    b = normalize_url_key("https://www.gov.cn/zhengce/content/2026-08/13/x.htm")
    assert a == b
    assert a.startswith("https://www.gov.cn/")


def test_titles_similar():
    assert titles_similar("国务院印发新能源指导意见", "国务院印发新能源指导意见 ")
    assert not titles_similar("国务院印发新能源指导意见", "央行下调存款准备金率")


def test_clamp_interval():
    assert clamp_interval(4, kind="trading") == 5
    assert clamp_interval(200, kind="trading") == 180
    assert clamp_interval(10, kind="offhours") == 15
    with pytest.raises(ValueError, match="整数"):
        clamp_interval("x", kind="trading")


def test_scan_window_trading_only_weekend(monkeypatch):
    monkeypatch.setattr(
        "app.advisor.policy_watch.schedule.is_trading_day", lambda _d: False
    )
    now = datetime(2026, 8, 15, 10, 0, tzinfo=SH)  # Saturday
    settings = {
        "scan_mode": "trading_only",
        "interval_trading_min": 15,
        "interval_offhours_min": 60,
    }
    assert in_user_scan_window(settings, now=now) is False


def test_scan_window_always_weekend(monkeypatch):
    monkeypatch.setattr(
        "app.advisor.policy_watch.schedule.is_trading_day", lambda _d: False
    )
    now = datetime(2026, 8, 15, 10, 0, tzinfo=SH)
    settings = {"scan_mode": "always", "interval_trading_min": 15, "interval_offhours_min": 60}
    assert in_user_scan_window(settings, now=now) is True
    assert current_interval_minutes(settings, now=now) == 60


def test_scan_window_trading_hours(monkeypatch):
    monkeypatch.setattr(
        "app.advisor.policy_watch.schedule.is_trading_day", lambda _d: True
    )
    now = datetime(2026, 8, 13, 10, 30, tzinfo=SH)
    settings = {
        "scan_mode": "trading_only",
        "interval_trading_min": 15,
        "interval_offhours_min": 60,
    }
    assert in_user_scan_window(settings, now=now) is True
    assert current_interval_minutes(settings, now=now) == 15
    night = datetime(2026, 8, 13, 20, 0, tzinfo=SH)
    assert in_user_scan_window(settings, now=night) is False


def test_user_interval_elapsed():
    now = datetime(2026, 8, 13, 10, 30, tzinfo=SH)
    settings = {
        "scan_mode": "always",
        "interval_trading_min": 15,
        "interval_offhours_min": 60,
        "last_fanout_at": datetime(2026, 8, 13, 10, 20, tzinfo=SH),
    }
    assert user_interval_elapsed(settings, now=now) is False
    settings["last_fanout_at"] = datetime(2026, 8, 13, 10, 0, tzinfo=SH)
    assert user_interval_elapsed(settings, now=now) is True
    settings["last_fanout_at"] = None
    assert user_interval_elapsed(settings, now=now) is True


def test_should_email_thresholds():
    policy = {"impact_score": 0.76, "category": "policy", "sectors": [], "symbols": []}
    mid = {"impact_score": 0.5, "category": "news", "sectors": [], "symbols": []}
    loose = {
        "impact_score": 0.3,
        "category": "news",
        "sectors": [{"name": "新能源", "reason": "x"}],
        "symbols": [],
    }
    assert should_email(policy, "low") is True
    assert should_email(mid, "low") is False
    assert should_email(mid, "medium") is True
    assert should_email(loose, "high") is True
    assert should_email({"impact_score": 0.3, "category": "news", "sectors": [], "symbols": []}, "high") is False
    assert direction_label("up") == "利好"
