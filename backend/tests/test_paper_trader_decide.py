from app.advisor.paper_trader.decide import normalize_actions, parse_decision_response


def test_parse_and_filter_signal_first():
    raw = """{"actions":[
      {"symbol":"600000","side":"buy","qty":100,"reason":"强势"},
      {"symbol":"999999","side":"buy","qty":100,"reason":"池外"},
      {"symbol":"600001","side":"buy","qty":100,"reason":"中性却买"}
    ]}"""
    parsed = parse_decision_response(raw)
    cands = [
        {"symbol": "600000", "direction": "buy"},
        {"symbol": "600001", "direction": "neutral"},
    ]
    intents = normalize_actions(
        parsed["actions"],
        candidates=cands,
        mode="signal_first",
        equity=100_000,
        quotes={"600000": {"price": 10}, "600001": {"price": 10}},
    )
    assert [i["symbol"] for i in intents] == ["600000"]


def test_target_weight_to_qty():
    intents = normalize_actions(
        [{"symbol": "600000", "side": "buy", "target_weight": 0.1, "reason": "w"}],
        candidates=[{"symbol": "600000", "direction": "buy"}],
        mode="signal_first",
        equity=100_000,
        quotes={"600000": {"price": 10}},
        lot_size=100,
    )
    # 100000 * 0.1 / 10 = 1000
    assert intents[0]["qty"] == 1000
