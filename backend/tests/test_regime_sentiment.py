from app.advisor.regime.sentiment import compute_sentiment_metrics

CFG = {
    "height_board_min": 3,
    "sentiment_weights": {
        "seal_rate": 0.25, "height": 0.25, "promotion": 0.25,
        "limit_up_count": 0.15, "limit_down_penalty": 0.10,
    },
    "cycle_thresholds": {"ice": 0.20, "repair": 0.35, "strengthen": 0.55, "climax": 0.75},
    "cycle_hysteresis": 0.0,
}

def test_seal_and_break_rates():
    m = compute_sentiment_metrics(
        {"sealed": [{"board_count": 1}] * 8, "broken": [{"board_count": 1}] * 2, "limit_down_count": 1},
        prev=None,
        cfg=CFG,
    )
    assert m["limit_up_count"] == 8
    assert m["broken_count"] == 2
    assert abs(m["seal_rate"] - 0.8) < 1e-6
    assert m["promotion_rate"] is None  # degraded input

def test_promotion_rate_two_day():
    today = {"sealed": [{"board_count": 2}] * 3 + [{"board_count": 1}] * 5, "broken": [], "limit_down_count": 0}
    prev = {"by_board": {1: 10, 2: 2}}  # yesterday 10 first-boards
    m = compute_sentiment_metrics(today, prev=prev, cfg=CFG)
    # 3 boards at 2 / 10 yesterday at 1 → 0.3
    assert abs(m["promotion_rate"] - 0.3) < 1e-6
