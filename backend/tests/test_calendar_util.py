from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed


def test_concurrent_last_trading_day_uses_single_akshare_load(monkeypatch):
    from app.advisor import calendar_util as cal

    calls = {"n": 0}

    def fake_hist():
        calls["n"] += 1
        import pandas as pd
        import time

        time.sleep(0.05)
        return pd.DataFrame({"trade_date": ["2026-07-31", "2026-08-01"]})

    class FakeAk:
        @staticmethod
        def tool_trade_date_hist_sina():
            return fake_hist()

    cal._trade_dates_set_unlocked.cache_clear()
    monkeypatch.setattr(cal, "_today", lambda: __import__("datetime").date(2026, 8, 2))

    import sys
    import types

    fake_mod = types.ModuleType("akshare")
    fake_mod.tool_trade_date_hist_sina = FakeAk.tool_trade_date_hist_sina
    monkeypatch.setitem(sys.modules, "akshare", fake_mod)

    # Patch inside the unlocked function's import path: re-bind by replacing unlocked
    # with a version that uses our fake via import akshare
    with ThreadPoolExecutor(max_workers=8) as pool:
        futs = [pool.submit(cal.last_trading_day) for _ in range(8)]
        results = [f.result(timeout=5) for f in as_completed(futs)]

    assert all(r == "2026-07-31" for r in results)
    assert calls["n"] == 1
