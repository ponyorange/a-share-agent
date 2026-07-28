from datetime import datetime, timedelta, timezone

from app.advisor.monitor.rules import (
    cooldown_key,
    evaluate_rule,
    is_cooled_down,
    mark_cooldown,
)


def test_price_and_chg_rules():
    q = {"price": 10.0, "day_chg_pct": -0.04}
    assert evaluate_rule({"type": "price_below", "value": 10.0}, q) is True
    assert evaluate_rule({"type": "price_above", "value": 10.5}, q) is False
    assert evaluate_rule({"type": "day_chg_below", "value": -0.03}, q) is True
    assert evaluate_rule({"type": "day_chg_above", "value": 0.03}, q) is False


def test_missing_price_skips():
    assert evaluate_rule({"type": "price_below", "value": 1}, {"price": None}) is False


def test_cooldown():
    now = datetime(2026, 7, 28, 10, 0, tzinfo=timezone.utc)
    key = cooldown_key("510300", "r1")
    cds = mark_cooldown({}, key, now)
    assert is_cooled_down(cds, key, now + timedelta(seconds=100), 1800) is False
    assert is_cooled_down(cds, key, now + timedelta(seconds=1801), 1800) is True
