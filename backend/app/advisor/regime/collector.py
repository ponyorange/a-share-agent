from __future__ import annotations

from datetime import datetime
from typing import Any, Callable
from zoneinfo import ZoneInfo

from app.limitup import build_ladder, merge_today_rows, normalize_pool_row

FetchRows = Callable[[str], Any]
FetchTrendFeatures = Callable[[str], dict[str, Any]]

SH = ZoneInfo("Asia/Shanghai")


def _today_trade_date() -> str:
    return datetime.now(SH).strftime("%Y-%m-%d")


def _date_yyyymmdd(trade_date: str) -> str:
    return trade_date.replace("-", "")


def _records_from_frame(frame: Any) -> list[dict[str, Any]]:
    if frame is None:
        return []
    if isinstance(frame, list):
        return [x for x in frame if isinstance(x, dict)]
    try:
        if getattr(frame, "empty", False):
            return []
        records = frame.to_dict(orient="records")
    except Exception:
        return []
    return [x for x in records if isinstance(x, dict)]


def _fetch_sealed_default(date_yyyymmdd: str) -> list[dict[str, Any]]:
    import akshare as ak

    return _records_from_frame(ak.stock_zt_pool_em(date=date_yyyymmdd))


def _fetch_broken_default(date_yyyymmdd: str) -> list[dict[str, Any]]:
    import akshare as ak

    return _records_from_frame(ak.stock_zt_pool_zbgc_em(date=date_yyyymmdd))


def _fetch_limit_down_default(date_yyyymmdd: str) -> list[dict[str, Any]]:
    import akshare as ak

    return _records_from_frame(ak.stock_zt_pool_dtgc_em(date=date_yyyymmdd))


def _ma_stack(latest: dict[str, Any]) -> str:
    close = latest.get("close")
    ma5 = latest.get("ma5")
    ma10 = latest.get("ma10")
    ma20 = latest.get("ma20")
    if None in (close, ma5, ma10, ma20):
        return "mixed"
    if close > ma5 > ma10 > ma20:
        return "above"
    if close < ma5 < ma10 < ma20:
        return "below"
    return "mixed"


def _fetch_trend_features_default(trade_date: str) -> dict[str, Any]:
    from app.kline import fetch_symbol_daily_ma
    from app.market import get_market

    ma_payload = fetch_symbol_daily_ma("000300", recent=60)
    latest = dict(ma_payload.get("latest") or {})
    recent = list(ma_payload.get("recent") or [])

    close = latest.get("close")
    highs = [
        float(x["high"])
        for x in recent
        if x.get("high") not in (None, "", "-")
    ]
    high_max = max(highs) if highs else None
    drawdown = 0.0
    if close is not None and high_max and high_max > 0:
        drawdown = max(0.0, 1.0 - float(close) / high_max)

    volumes = [
        float(x["volume"])
        for x in recent[-20:]
        if x.get("volume") not in (None, "", "-")
    ]
    latest_volume = None
    if recent and recent[-1].get("volume") not in (None, "", "-"):
        latest_volume = float(recent[-1]["volume"])
    volume_vs_ma20 = 1.0
    if latest_volume is not None and volumes:
        avg_volume = sum(volumes) / len(volumes)
        if avg_volume > 0:
            volume_vs_ma20 = latest_volume / avg_volume

    indices = list((get_market().get("featured") or []))
    changed = [x for x in indices if x.get("change_pct") is not None]
    breadth = 0.0
    if changed:
        breadth = sum(
            1 for x in changed if float(x.get("change_pct") or 0.0) > 0
        ) / len(changed)

    return {
        "trade_date": trade_date,
        "index_symbol": ma_payload.get("symbol") or "000300",
        "ma_stack": _ma_stack(latest),
        "drawdown_from_high": drawdown,
        "breadth": breadth,
        "volume_vs_ma20": volume_vs_ma20,
    }


def _safe_fetch_rows(
    label: str,
    fetcher: FetchRows,
    date_yyyymmdd: str,
    errors: list[str],
) -> list[dict[str, Any]]:
    try:
        return _records_from_frame(fetcher(date_yyyymmdd))
    except Exception as exc:
        errors.append(f"{label}: {type(exc).__name__}: {exc}")
        return []


def collect_raw(
    trade_date: str | None = None,
    *,
    fetch_sealed: FetchRows = _fetch_sealed_default,
    fetch_broken: FetchRows = _fetch_broken_default,
    fetch_limit_down: FetchRows = _fetch_limit_down_default,
    fetch_trend_features: FetchTrendFeatures = _fetch_trend_features_default,
) -> dict:
    date = trade_date or _today_trade_date()
    date_key = _date_yyyymmdd(date)
    errors: list[str] = []

    sealed_raw = _safe_fetch_rows("sealed", fetch_sealed, date_key, errors)
    broken_raw = _safe_fetch_rows("broken", fetch_broken, date_key, errors)
    limit_down_raw = _safe_fetch_rows("limit_down", fetch_limit_down, date_key, errors)

    sealed = [
        item
        for item in (normalize_pool_row(row, status="sealed") for row in sealed_raw)
        if item
    ]
    broken = [
        item
        for item in (normalize_pool_row(row, status="broken") for row in broken_raw)
        if item
    ]
    ladder = build_ladder(merge_today_rows(sealed, broken))

    try:
        trend_features = dict(fetch_trend_features(date) or {})
    except Exception as exc:
        errors.append(f"trend_features: {type(exc).__name__}: {exc}")
        trend_features = {}

    return {
        "trade_date": date,
        "sealed": sealed,
        "broken": broken,
        "limit_down_count": len(limit_down_raw),
        "ladder_max": int(ladder[0]["board_count"]) if ladder else 0,
        "trend_features": trend_features,
        "errors": errors,
    }
