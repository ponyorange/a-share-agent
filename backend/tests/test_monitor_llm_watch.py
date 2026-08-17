from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from app.advisor.monitor.llm_watch import (
    actions_to_notify,
    parse_watch_response,
    run_llm_watch,
    should_run_llm_watch,
)


def test_interval_trigger():
    now = datetime(2026, 7, 28, 10, 0, tzinfo=timezone.utc)
    job = {
        "llm_enabled": True,
        "llm_interval_sec": 900,
        "last_llm_at": None,
        "llm_anomaly_abs_chg": 0.03,
        "llm_symbol_baselines": {},
    }
    ok, syms = should_run_llm_watch(
        job, {"510300": {"day_chg_pct": 0.01}}, now
    )
    assert ok and "510300" in syms


def test_anomaly_trigger():
    now = datetime(2026, 7, 28, 10, 0, tzinfo=timezone.utc)
    job = {
        "llm_enabled": True,
        "llm_interval_sec": 900,
        "last_llm_at": (now - timedelta(seconds=60)).isoformat(),
        "llm_anomaly_abs_chg": 0.03,
        "llm_symbol_baselines": {"510300": 0.0},
    }
    ok, _ = should_run_llm_watch(
        job, {"510300": {"day_chg_pct": 0.04}}, now
    )
    assert ok is True


def test_no_trigger_within_interval_without_anomaly():
    now = datetime(2026, 7, 28, 10, 0, tzinfo=timezone.utc)
    job = {
        "llm_enabled": True,
        "llm_interval_sec": 900,
        "last_llm_at": (now - timedelta(seconds=60)).isoformat(),
        "llm_anomaly_abs_chg": 0.03,
        "llm_symbol_baselines": {"510300": 0.01},
    }
    ok, _ = should_run_llm_watch(
        job, {"510300": {"day_chg_pct": 0.015}}, now
    )
    assert ok is False


def test_parse_and_filter_hold():
    raw = (
        '{"symbols":[{"symbol":"510300","action":"hold","confidence":0.5,'
        '"rationale":"x","catalysts":[]},{"symbol":"159915","action":"buy",'
        '"confidence":0.7,"rationale":"y","catalysts":["news"]}],'
        '"market_note":"震荡"}'
    )
    parsed = parse_watch_response(raw)
    items = actions_to_notify(parsed)
    assert len(items) == 1 and items[0]["action"] == "buy"


def test_run_llm_watch_hold_does_not_mail(monkeypatch):
    class _Msg:
        content = (
            '{"symbols":[{"symbol":"510300","action":"hold","confidence":0.5,'
            '"rationale":"观望","catalysts":[]}],"market_note":"平"}'
        )

    class _Model:
        def invoke(self, _msgs):
            return _Msg()

    sent: list[Any] = []
    import app.advisor.monitor.llm_watch as lw

    monkeypatch.setattr(lw, "build_watch_context", lambda *a, **k: "ctx")
    monkeypatch.setattr(
        "app.advisor.agent.llm.build_chat_model",
        lambda *a, **k: _Model(),
    )
    monkeypatch.setattr(
        lw, "send_watch_digest_email", lambda **kw: sent.append(kw)
    )
    monkeypatch.setattr(
        "app.advisor.llm_settings.resolve_llm_credentials",
        lambda *_a, **_k: {"api_key": "x", "model": "m", "base_url": "http://x"},
    )

    out = run_llm_watch(
        "u1",
        {
            "id": "j1",
            "title": "t",
            "notify_email": "a@x.com",
            "cooldown_sec": 1800,
            "knowledge_ids": [],
        },
        ["510300"],
        {"510300": {"price": 1, "day_chg_pct": 0.01}},
    )
    assert out["ok"] is True
    assert out["notified"] == 0
    assert sent == []


def test_run_llm_watch_buy_sends(monkeypatch):
    class _Msg:
        content = (
            '{"symbols":[{"symbol":"510300","action":"buy","confidence":0.8,'
            '"rationale":"突破","catalysts":["政策"]}],"market_note":"强"}'
        )

    class _Model:
        def invoke(self, _msgs):
            return _Msg()

    sent: list[Any] = []
    import app.advisor.monitor.llm_watch as lw

    monkeypatch.setattr(lw, "build_watch_context", lambda *a, **k: "ctx")
    monkeypatch.setattr(
        "app.advisor.agent.llm.build_chat_model",
        lambda *a, **k: _Model(),
    )
    monkeypatch.setattr(
        lw, "send_watch_digest_email", lambda **kw: sent.append(kw)
    )
    monkeypatch.setattr(
        "app.advisor.llm_settings.resolve_llm_credentials",
        lambda *_a, **_k: {"api_key": "x", "model": "m", "base_url": "http://x"},
    )

    out = run_llm_watch(
        "u1",
        {
            "id": "j1",
            "title": "t",
            "notify_email": "a@x.com",
            "cooldown_sec": 1800,
            "knowledge_ids": [],
        },
        ["510300"],
        {"510300": {"price": 1, "day_chg_pct": 0.04}},
    )
    assert out["ok"] is True
    assert out["notified"] == 1
    assert len(sent) == 1
