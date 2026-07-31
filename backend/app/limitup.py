"""A-share limit-up board (涨停池 + 炸板池 → 当天涨停 / 连板天梯)."""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

SH = ZoneInfo("Asia/Shanghai")
CACHE_TTL_SEC = 6.0

_cache: dict[str, Any] = {"ts": 0.0, "payload": None}


def _as_symbol(raw: Any) -> str:
    text = str(raw or "").strip()
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) >= 6:
        return digits[-6:]
    return digits.zfill(6) if digits else ""


def _as_chg_ratio(raw: Any) -> float | None:
    if raw is None or raw == "" or raw == "-":
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    # Eastmoney pools usually return percent (10.0 = 10%).
    if abs(value) > 1.0:
        return value / 100.0
    return value


def _as_board_count(row: dict[str, Any]) -> int:
    for key in ("连板数", "昨日连板数"):
        if key in row and row.get(key) not in (None, "", "-"):
            try:
                n = int(float(row[key]))
                if n > 0:
                    return n
            except (TypeError, ValueError):
                pass
    # e.g. "4/3" → first number is consecutive-ish stats
    stats = row.get("涨停统计")
    if isinstance(stats, str) and "/" in stats:
        head = stats.split("/", 1)[0].strip()
        try:
            n = int(head)
            if n > 0:
                return n
        except ValueError:
            pass
    return 1


def _as_limit_price(row: dict[str, Any]) -> float | None:
    for key in ("涨停价", "limit_up"):
        if key in row and row.get(key) not in (None, "", "-"):
            try:
                return float(row[key])
            except (TypeError, ValueError):
                return None
    return None


def normalize_pool_row(row: dict[str, Any], *, status: str) -> dict[str, Any] | None:
    symbol = _as_symbol(
        row.get("代码") or row.get("股票代码") or row.get("证券代码")
    )
    if not symbol:
        return None
    name = str(row.get("名称") or row.get("股票简称") or symbol).strip()
    return {
        "symbol": symbol,
        "name": name,
        "day_chg_pct": _as_chg_ratio(row.get("涨跌幅")),
        "board_count": _as_board_count(row),
        "status": status,
        "limit_up_price": _as_limit_price(row),
    }


def merge_today_rows(
    sealed: list[dict[str, Any]],
    broken: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_symbol: dict[str, dict[str, Any]] = {}
    for row in broken:
        by_symbol[row["symbol"]] = row
    for row in sealed:
        by_symbol[row["symbol"]] = row  # sealed wins
    out = list(by_symbol.values())
    out.sort(
        key=lambda r: (
            0 if r.get("status") == "sealed" else 1,
            -(r.get("board_count") or 0),
            -(r.get("day_chg_pct") or 0.0),
            r.get("symbol") or "",
        )
    )
    return out


def build_ladder(today: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[int, list[dict[str, Any]]] = {}
    for row in today:
        if row.get("status") != "sealed":
            continue
        try:
            n = int(row.get("board_count") or 1)
        except (TypeError, ValueError):
            n = 1
        if n < 1:
            n = 1
        buckets.setdefault(n, []).append(
            {
                "symbol": row["symbol"],
                "name": row["name"],
                "day_chg_pct": row.get("day_chg_pct"),
            }
        )
    ladder: list[dict[str, Any]] = []
    for n in sorted(buckets.keys(), reverse=True):
        items = sorted(
            buckets[n],
            key=lambda x: (-(x.get("day_chg_pct") or 0.0), x.get("symbol") or ""),
        )
        ladder.append({"board_count": n, "items": items})
    return ladder


def _pool_date_yyyymmdd() -> str:
    now = datetime.now(SH)
    return now.strftime("%Y%m%d")


def _records_from_df(frame: Any) -> list[dict[str, Any]]:
    if frame is None:
        return []
    try:
        if getattr(frame, "empty", False):
            return []
        return frame.to_dict(orient="records")
    except Exception:
        return []


def _fetch_pools(date_yyyymmdd: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    import akshare as ak

    try:
        zt = ak.stock_zt_pool_em(date=date_yyyymmdd)
    except Exception as exc:
        logger.warning("stock_zt_pool_em failed: %s", exc)
        raise RuntimeError(f"涨停池拉取失败: {type(exc).__name__}: {exc}") from exc
    try:
        zbgc = ak.stock_zt_pool_zbgc_em(date=date_yyyymmdd)
    except Exception as exc:
        logger.warning("stock_zt_pool_zbgc_em failed: %s", exc)
        # 炸板池失败时仍返回封板池
        zbgc = None
    return _records_from_df(zt), _records_from_df(zbgc)


def get_limit_up(*, force: bool = False) -> dict[str, Any]:
    """Build limit-up board payload (cached briefly for polling clients)."""
    now_mono = time.monotonic()
    cached = _cache.get("payload")
    if (
        not force
        and cached is not None
        and (now_mono - float(_cache.get("ts") or 0.0)) < CACHE_TTL_SEC
    ):
        return cached

    from .quote import trading_session

    session = trading_session()
    date_key = _pool_date_yyyymmdd()
    zt_rows, zbgc_rows = _fetch_pools(date_key)

    sealed: list[dict[str, Any]] = []
    for raw in zt_rows:
        if not isinstance(raw, dict):
            continue
        item = normalize_pool_row(raw, status="sealed")
        if item:
            sealed.append(item)

    broken: list[dict[str, Any]] = []
    for raw in zbgc_rows:
        if not isinstance(raw, dict):
            continue
        item = normalize_pool_row(raw, status="broken")
        if item:
            broken.append(item)

    today = merge_today_rows(sealed, broken)
    ladder = build_ladder(today)
    as_of = datetime.now(SH).isoformat(timespec="seconds")
    payload = {
        "as_of": as_of,
        "date": f"{date_key[:4]}-{date_key[4:6]}-{date_key[6:8]}",
        "session": {
            "is_trading": bool(session.get("is_trading")),
            "is_trading_day": bool(session.get("is_trading_day")),
        },
        "today": today,
        "ladder": ladder,
    }
    _cache["ts"] = now_mono
    _cache["payload"] = payload
    return payload
