"""Individual main-force fund flow snapshot for monitor rules."""

from __future__ import annotations

import logging
from typing import Any

from ...kline import normalize_symbol

logger = logging.getLogger(__name__)

_CACHE: dict[str, dict[str, Any]] = {}


def _num(raw: Any) -> float | None:
    if raw is None:
        return None
    try:
        return float(str(raw).replace(",", "").replace("%", "").strip())
    except (TypeError, ValueError):
        return None


def _pick_col(columns: Any, *, prefer_main: bool) -> Any | None:
    cols = list(columns)
    if prefer_main:
        for c in cols:
            s = str(c)
            if "主力净流入" in s or s in ("主力净流入-净额",):
                return c
        for c in cols:
            s = str(c)
            if "净流入" in s and "占比" not in s:
                return c
        return None
    for c in cols:
        s = str(c)
        if "成交额" in s or s in ("amount", "Amount"):
            return c
    return None


def get_flow_snapshot(symbol: str, *, window_days: int = 5) -> dict[str, Any]:
    """Latest main-force net inflow + window average + optional turnover ratio."""
    try:
        sym = normalize_symbol(symbol)
    except ValueError as exc:
        return {
            "ok": False,
            "symbol": symbol,
            "net_inflow": None,
            "avg_net_inflow": None,
            "avg_abs_net": None,
            "amount": None,
            "ratio": None,
            "error": str(exc),
        }

    window = max(1, min(int(window_days or 5), 20))
    cache_key = f"{sym}:{window}"
    if cache_key in _CACHE:
        return dict(_CACHE[cache_key])

    out: dict[str, Any] = {
        "ok": False,
        "symbol": sym,
        "net_inflow": None,
        "avg_net_inflow": None,
        "avg_abs_net": None,
        "amount": None,
        "ratio": None,
        "error": None,
    }
    market = "sh" if sym.startswith(("5", "6", "9")) else "sz"
    try:
        import akshare as ak

        df = None
        for caller in (
            lambda: ak.stock_individual_fund_flow(stock=sym, market=market),
            lambda: ak.stock_individual_fund_flow(symbol=sym),
        ):
            try:
                df = caller()
                if df is not None and not getattr(df, "empty", True):
                    break
            except TypeError:
                continue
            except Exception:
                continue
        if df is None or getattr(df, "empty", True):
            out["error"] = "empty_flow"
            _CACHE[cache_key] = dict(out)
            return out

        net_col = _pick_col(df.columns, prefer_main=True)
        if net_col is None:
            out["error"] = "no_net_inflow_col"
            _CACHE[cache_key] = dict(out)
            return out

        nets: list[float] = []
        for v in df[net_col].tolist():
            n = _num(v)
            if n is not None:
                nets.append(n)
        if not nets:
            out["error"] = "no_net_inflow_values"
            _CACHE[cache_key] = dict(out)
            return out

        net = nets[-1]
        prior = nets[-(window + 1) : -1] if len(nets) > 1 else nets[:-1]
        if not prior:
            prior = nets[-min(window, len(nets)) :]
        avg = sum(prior) / len(prior) if prior else net
        out["net_inflow"] = float(net)
        out["avg_net_inflow"] = float(avg)
        out["avg_abs_net"] = abs(float(avg))

        amt_col = _pick_col(df.columns, prefer_main=False)
        if amt_col is not None:
            amounts: list[float] = []
            for v in df[amt_col].tolist():
                a = _num(v)
                if a is not None:
                    amounts.append(a)
            if amounts:
                amount = amounts[-1]
                out["amount"] = float(amount)
                if amount and abs(amount) > 0:
                    out["ratio"] = float(net) / float(amount)

        out["ok"] = True
    except Exception as exc:
        out["error"] = f"{type(exc).__name__}: {exc}"
        logger.warning("flow snapshot failed %s: %s", sym, exc)

    _CACHE[cache_key] = dict(out)
    return out


def clear_flow_cache() -> None:
    _CACHE.clear()
