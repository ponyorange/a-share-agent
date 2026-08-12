from app.advisor.paper_trader.risk import (
    filter_intents,
    is_near_limit_board,
    should_halt_for_daily_loss,
)

RISK = {
    "max_single_position": 0.25,
    "max_total_exposure": 0.90,
    "max_positions": 10,
    "max_trades_per_day": 30,
    "max_daily_loss_pct": 0.05,
    "lot_size": 100,
    "block_limit_board": True,
}


def test_blocks_over_single_position():
    account = {
        "cash": 50_000,
        "equity": 100_000,
        "positions": [{"symbol": "600000", "qty": 0, "last": 10.0}],
    }
    quotes = {"600000": {"price": 10.0, "day_chg_pct": 0.01}}
    # 买 3000 股 = 30000 > 25% * 100000
    allowed, blocked = filter_intents(
        [{"symbol": "600000", "side": "buy", "qty": 3000}],
        account=account,
        quotes_by_symbol=quotes,
        risk=RISK,
        trades_today=0,
        equity_day_open=100_000,
    )
    assert allowed == []
    assert blocked[0]["reason"] == "max_single_position"


def test_blocks_limit_up_heuristic():
    assert is_near_limit_board({"price": 11.0, "day_chg_pct": 0.096}) is True
    assert is_near_limit_board({"price": 10.0, "day_chg_pct": 0.01}) is False


def test_daily_loss_halt():
    assert should_halt_for_daily_loss(
        equity=94_000, equity_day_open=100_000, max_daily_loss_pct=0.05
    )
    assert not should_halt_for_daily_loss(
        equity=96_000, equity_day_open=100_000, max_daily_loss_pct=0.05
    )
