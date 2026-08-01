"""A-share limit-up board (涨停池 + 炸板池 → 当天涨停 / 连板天梯)."""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

SH = ZoneInfo("Asia/Shanghai")
CACHE_TTL_SEC = 6.0
FLOW_CACHE_TTL_SEC = 20.0
ULIST_BATCH = 80
STOCK_FLOW_WORKERS = 8
STOCK_FLOW_TIMEOUT = 5.0

_cache: dict[str, Any] = {"ts": 0.0, "payload": None}
_flow_cache: dict[str, Any] = {"ts": 0.0, "by_symbol": {}}


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


def parse_flow_num(raw: Any) -> float | None:
    if raw is None or raw == "" or raw == "-":
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


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
        "main_inflow": None,
        "main_outflow": None,
        "main_net_inflow": None,
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
                "main_inflow": row.get("main_inflow"),
                "main_outflow": row.get("main_outflow"),
                "main_net_inflow": row.get("main_net_inflow"),
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


def apply_fund_flow(
    today: list[dict[str, Any]],
    flow_by_symbol: dict[str, dict[str, Any]],
) -> None:
    """Mutate today rows in place with fund-flow fields."""
    for row in today:
        flow = flow_by_symbol.get(str(row.get("symbol") or "")) or {}
        row["main_inflow"] = flow.get("main_inflow")
        row["main_outflow"] = flow.get("main_outflow")
        row["main_net_inflow"] = flow.get("main_net_inflow")


def _fetch_ulist_net(symbols: list[str]) -> dict[str, dict[str, Any]]:
    from .kline import _session, secid

    out: dict[str, dict[str, Any]] = {}
    if not symbols:
        return out
    sess = _session()
    for i in range(0, len(symbols), ULIST_BATCH):
        chunk = symbols[i : i + ULIST_BATCH]
        secids = ",".join(secid(s) for s in chunk)
        try:
            r = sess.get(
                "https://push2delay.eastmoney.com/api/qt/ulist.np/get",
                params={
                    "fltt": "2",
                    "invt": "2",
                    "fields": "f12,f62",
                    "secids": secids,
                },
                timeout=12,
            )
            r.raise_for_status()
            diff = (r.json().get("data") or {}).get("diff") or []
        except Exception as exc:
            logger.warning("limitup ulist fund net failed: %s", exc)
            continue
        for row in diff:
            if not isinstance(row, dict):
                continue
            sym = _as_symbol(row.get("f12"))
            if not sym:
                continue
            out[sym] = {"main_net_inflow": parse_flow_num(row.get("f62"))}
    return out


def _fetch_stock_flow(symbol: str) -> dict[str, Any]:
    from .kline import _session, secid

    r = _session().get(
        "https://push2delay.eastmoney.com/api/qt/stock/get",
        params={
            "fltt": "2",
            "invt": "2",
            "secid": secid(symbol),
            "fields": "f12,f135,f136,f137",
        },
        timeout=STOCK_FLOW_TIMEOUT,
    )
    r.raise_for_status()
    data = r.json().get("data") or {}
    if not isinstance(data, dict):
        raise RuntimeError("empty stock flow")
    return {
        "main_inflow": parse_flow_num(data.get("f135")),
        "main_outflow": parse_flow_num(data.get("f136")),
        "main_net_inflow": parse_flow_num(data.get("f137")),
    }


def enrich_fund_flow(
    symbols: list[str],
    *,
    force: bool = False,
) -> dict[str, dict[str, Any]]:
    """Return per-symbol main_inflow / main_outflow / main_net_inflow (yuan)."""
    uniq = sorted({_as_symbol(s) for s in symbols if _as_symbol(s)})
    now_mono = time.monotonic()
    cached: dict[str, dict[str, Any]] = dict(_flow_cache.get("by_symbol") or {})
    if (
        not force
        and cached
        and (now_mono - float(_flow_cache.get("ts") or 0.0)) < FLOW_CACHE_TTL_SEC
        and all(s in cached for s in uniq)
    ):
        return {s: dict(cached[s]) for s in uniq}

    out: dict[str, dict[str, Any]] = {
        s: {
            "main_inflow": None,
            "main_outflow": None,
            "main_net_inflow": None,
        }
        for s in uniq
    }
    try:
        nets = _fetch_ulist_net(uniq)
        for sym, payload in nets.items():
            if sym in out:
                out[sym]["main_net_inflow"] = payload.get("main_net_inflow")
    except Exception as exc:
        logger.warning("limitup ulist enrichment failed: %s", exc)

    if uniq:
        with ThreadPoolExecutor(max_workers=STOCK_FLOW_WORKERS) as pool:
            futs = {pool.submit(_fetch_stock_flow, s): s for s in uniq}
            for fut in as_completed(futs):
                sym = futs[fut]
                try:
                    payload = fut.result()
                except Exception as exc:
                    logger.debug("limitup stock flow %s failed: %s", sym, exc)
                    continue
                row = out[sym]
                if payload.get("main_inflow") is not None:
                    row["main_inflow"] = payload["main_inflow"]
                if payload.get("main_outflow") is not None:
                    row["main_outflow"] = payload["main_outflow"]
                # Prefer stock/get net when present; else keep ulist
                if payload.get("main_net_inflow") is not None:
                    row["main_net_inflow"] = payload["main_net_inflow"]

    _flow_cache["ts"] = now_mono
    merged = dict(cached)
    merged.update(out)
    _flow_cache["by_symbol"] = merged
    return out


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
    try:
        flow = enrich_fund_flow([r["symbol"] for r in today], force=force)
        apply_fund_flow(today, flow)
    except Exception as exc:
        logger.warning("limitup fund enrich skipped: %s", exc)

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
