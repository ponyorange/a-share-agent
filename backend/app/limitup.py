"""A-share limit-up board (涨停池 + 炸板池 → 当天涨停 / 连板天梯)."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterator
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


def _as_money(row: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        if key in row and row.get(key) not in (None, "", "-"):
            try:
                return float(row[key])
            except (TypeError, ValueError):
                return None
    return None


def _as_int_field(row: dict[str, Any], *keys: str) -> int | None:
    for key in keys:
        if key in row and row.get(key) not in (None, "", "-"):
            try:
                return int(float(row[key]))
            except (TypeError, ValueError):
                return None
    return None


def _as_seal_time(row: dict[str, Any], *keys: str) -> str | None:
    """Normalize Eastmoney HHMMSS / HH:MM:SS → HH:MM:SS."""
    for key in keys:
        raw = row.get(key)
        if raw in (None, "", "-"):
            continue
        text = str(raw).strip()
        digits = "".join(ch for ch in text if ch.isdigit())
        if len(digits) >= 6:
            hh, mm, ss = digits[:2], digits[2:4], digits[4:6]
            return f"{hh}:{mm}:{ss}"
        if len(digits) == 4:
            return f"{digits[:2]}:{digits[2:4]}:00"
        if ":" in text:
            return text[:8]
    return None


def normalize_pool_row(row: dict[str, Any], *, status: str) -> dict[str, Any] | None:
    symbol = _as_symbol(
        row.get("代码") or row.get("股票代码") or row.get("证券代码")
    )
    if not symbol:
        return None
    name = str(row.get("名称") or row.get("股票简称") or symbol).strip()
    industry = str(row.get("所属行业") or "").strip() or None
    return {
        "symbol": symbol,
        "name": name,
        "day_chg_pct": _as_chg_ratio(row.get("涨跌幅")),
        "board_count": _as_board_count(row),
        "status": status,
        "limit_up_price": _as_limit_price(row),
        "seal_funds": _as_money(row, "封板资金"),
        "first_seal_time": _as_seal_time(row, "首次封板时间"),
        "last_seal_time": _as_seal_time(row, "最后封板时间"),
        "break_count": _as_int_field(row, "炸板次数"),
        "turnover_pct": parse_flow_num(row.get("换手率")),
        "industry": industry,
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
    on_progress: Callable[[int, int], None] | None = None,
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
        if on_progress and uniq:
            on_progress(len(uniq), len(uniq))
        return {s: dict(cached[s]) for s in uniq}

    out: dict[str, dict[str, Any]] = {
        s: {
            "main_inflow": None,
            "main_outflow": None,
            "main_net_inflow": None,
        }
        for s in uniq
    }
    total = len(uniq)
    if on_progress and total:
        on_progress(0, total)
    try:
        nets = _fetch_ulist_net(uniq)
        for sym, payload in nets.items():
            if sym in out:
                out[sym]["main_net_inflow"] = payload.get("main_net_inflow")
    except Exception as exc:
        logger.warning("limitup ulist enrichment failed: %s", exc)

    if uniq:
        step = max(1, total // 10) if total else 1
        done = 0
        with ThreadPoolExecutor(max_workers=STOCK_FLOW_WORKERS) as pool:
            futs = {pool.submit(_fetch_stock_flow, s): s for s in uniq}
            for fut in as_completed(futs):
                sym = futs[fut]
                done += 1
                try:
                    payload = fut.result()
                except Exception as exc:
                    logger.debug("limitup stock flow %s failed: %s", sym, exc)
                    if on_progress and (done == total or done % step == 0):
                        on_progress(done, total)
                    continue
                row = out[sym]
                if payload.get("main_inflow") is not None:
                    row["main_inflow"] = payload["main_inflow"]
                if payload.get("main_outflow") is not None:
                    row["main_outflow"] = payload["main_outflow"]
                # Prefer stock/get net when present; else keep ulist
                if payload.get("main_net_inflow") is not None:
                    row["main_net_inflow"] = payload["main_net_inflow"]
                if on_progress and (done == total or done % step == 0):
                    on_progress(done, total)

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


def iter_limit_up_events(*, force: bool = False) -> Iterator[dict[str, Any]]:
    """Yield SSE-ready events: meta → progress* → done | error."""
    now_mono = time.monotonic()
    cached = _cache.get("payload")
    if (
        not force
        and cached is not None
        and (now_mono - float(_cache.get("ts") or 0.0)) < CACHE_TTL_SEC
    ):
        yield {
            "event": "meta",
            "data": {
                "force": force,
                "cached": True,
                "date": cached.get("date"),
            },
        }
        yield {
            "event": "progress",
            "data": {"phase": "cache", "message": "命中短缓存…"},
        }
        yield {"event": "done", "data": cached}
        return

    date_key = _pool_date_yyyymmdd()
    date_fmt = f"{date_key[:4]}-{date_key[4:6]}-{date_key[6:8]}"
    yield {
        "event": "meta",
        "data": {"force": force, "cached": False, "date": date_fmt},
    }
    try:
        from .quote import trading_session

        session = trading_session()
        yield {
            "event": "progress",
            "data": {
                "phase": "pool",
                "message": "正在拉取涨停池 / 炸板池…",
            },
        }
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
        symbols = [r["symbol"] for r in today]
        flow_state: dict[str, int] = {"done": 0, "total": len(symbols)}

        def _flow_progress(done: int, total: int) -> None:
            flow_state["done"] = done
            flow_state["total"] = total

        yield {
            "event": "progress",
            "data": {
                "phase": "fund_flow",
                "message": "正在补充主力资金流…",
                "done": 0,
                "total": len(symbols),
            },
        }
        try:
            flow = enrich_fund_flow(
                symbols,
                force=force,
                on_progress=_flow_progress,
            )
            apply_fund_flow(today, flow)
            if symbols:
                yield {
                    "event": "progress",
                    "data": {
                        "phase": "fund_flow",
                        "message": "主力资金流补充完成",
                        "done": flow_state["done"] or len(symbols),
                        "total": flow_state["total"] or len(symbols),
                    },
                }
        except Exception as exc:
            logger.warning("limitup fund enrich skipped: %s", exc)
            yield {
                "event": "progress",
                "data": {
                    "phase": "fund_flow",
                    "message": "主力资金流补充跳过（部分失败）",
                    "done": flow_state["done"],
                    "total": flow_state["total"] or len(symbols),
                },
            }

        yield {
            "event": "progress",
            "data": {"phase": "build", "message": "正在组装连板天梯…"},
        }
        ladder = build_ladder(today)
        as_of = datetime.now(SH).isoformat(timespec="seconds")
        payload = {
            "as_of": as_of,
            "date": date_fmt,
            "session": {
                "is_trading": bool(session.get("is_trading")),
                "is_trading_day": bool(session.get("is_trading_day")),
            },
            "today": today,
            "ladder": ladder,
        }
        _cache["ts"] = time.monotonic()
        _cache["payload"] = payload
        yield {"event": "done", "data": payload}
    except Exception as exc:
        yield {
            "event": "error",
            "data": {"detail": f"{type(exc).__name__}: {exc}"},
        }


def get_limit_up_status_map_for_date(trade_date: str) -> dict[str, str]:
    """Return symbol -> sealed|broken for a historical trading day (no fund-flow).

    Sealed wins over broken when a symbol appears in both pools.
    """
    raw = str(trade_date or "").strip()
    if "-" in raw:
        date_key = raw.replace("-", "")[:8]
    else:
        date_key = "".join(ch for ch in raw if ch.isdigit())[:8]
    if len(date_key) != 8:
        raise ValueError(f"invalid trade_date: {trade_date}")

    zt_rows, zbgc_rows = _fetch_pools(date_key)
    out: dict[str, str] = {}
    for row in zt_rows:
        if not isinstance(row, dict):
            continue
        item = normalize_pool_row(row, status="sealed")
        if item:
            out[str(item["symbol"])] = "sealed"
    for row in zbgc_rows or []:
        if not isinstance(row, dict):
            continue
        item = normalize_pool_row(row, status="broken")
        if not item:
            continue
        symbol = str(item["symbol"])
        if symbol not in out:
            out[symbol] = "broken"
    return out


def get_limit_up(*, force: bool = False) -> dict[str, Any]:
    """Build limit-up board payload (cached briefly for polling clients)."""
    last_error: str | None = None
    for ev in iter_limit_up_events(force=force):
        if ev.get("event") == "done":
            data = ev.get("data")
            if isinstance(data, dict):
                return data
        if ev.get("event") == "error":
            detail = (ev.get("data") or {}).get("detail")
            last_error = str(detail or "打板数据获取失败")
    raise RuntimeError(last_error or "打板数据获取失败")
