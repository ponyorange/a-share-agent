"""Market / sector / flow / valuation context for multi-factor scoring (V2).

All fetchers degrade to neutral (0.5) on failure. Results cached in Mongo
collection `advisor_context_cache` with TTL-ish overwrite by key+day.
"""

from __future__ import annotations

import math
import time
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

import numpy as np

from ..db import get_db
from ..kline import normalize_symbol
from .calendar_util import last_trading_day
from .config_loader import load_config
from .features import load_benchmark

_NEUTRAL = 0.5
_CACHE_TTL_SEC = 3600  # in-process soft TTL; Mongo keyed by trade_date


def _clip01(v: float | None) -> float:
    if v is None or (isinstance(v, float) and (np.isnan(v) or np.isinf(v))):
        return _NEUTRAL
    return float(max(0.0, min(1.0, v)))


def _finite_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _json_safe(value: Any) -> Any:
    """Replace NaN/Inf with None so Mongo/cache/snapshots stay JSON-safe."""
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, (float, np.floating)):
        number = float(value)
        return number if math.isfinite(number) else None
    if isinstance(value, (np.integer,)):
        return int(value)
    return value


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _cache_get(key: str, trade_date: str) -> Any | None:
    try:
        doc = get_db().advisor_context_cache.find_one(
            {"key": key, "trade_date": trade_date}, {"_id": 0}
        )
        if not doc:
            return None
        ts = doc.get("fetched_at_ts") or 0
        if time.time() - float(ts) > _CACHE_TTL_SEC:
            return None
        return _json_safe(doc.get("payload"))
    except Exception:
        return None


def _cache_set(key: str, trade_date: str, payload: Any) -> None:
    try:
        get_db().advisor_context_cache.update_one(
            {"key": key, "trade_date": trade_date},
            {
                "$set": {
                    "key": key,
                    "trade_date": trade_date,
                    "payload": _json_safe(payload),
                    "fetched_at": _now_iso(),
                    "fetched_at_ts": time.time(),
                }
            },
            upsert=True,
        )
    except Exception:
        pass


def _session_ak():
    import akshare as ak

    return ak


# ---------- 北向 / 市场 ----------


def fetch_northbound_net_score(trade_date: str | None = None) -> dict[str, Any]:
    """北向近几日净买入 → 0~1。失败中性。"""
    day = (trade_date or last_trading_day())[:10]
    cached = _cache_get("northbound", day)
    if cached is not None:
        return cached

    out: dict[str, Any] = {
        "score": _NEUTRAL,
        "net_yi": None,
        "source": "akshare.stock_hsgt_hist_em",
        "ok": False,
    }
    try:
        ak = _session_ak()
        df = ak.stock_hsgt_hist_em(symbol="北向资金")
        if df is not None and not df.empty:
            # 当日成交净买额 单位：亿元
            col = "当日成交净买额"
            if col not in df.columns:
                for c in df.columns:
                    if "净买" in str(c):
                        col = c
                        break
            tail = df.tail(5)
            vals = []
            for v in tail[col].tolist():
                number = _finite_float(v)
                if number is not None:
                    vals.append(number)
            if vals:
                # 近 5 日均值：约 ±50 亿映射到 0~1
                avg = float(np.mean(vals))
                if math.isfinite(avg):
                    out["net_yi"] = round(avg, 2)
                    out["score"] = _clip01((avg + 50.0) / 100.0)
                    out["ok"] = True
    except Exception as exc:
        out["error"] = f"{type(exc).__name__}: {exc}"

    out = _json_safe(out)
    _cache_set("northbound", day, out)
    return out


def fetch_benchmark_trend_score(as_of: str | None = None) -> dict[str, Any]:
    """沪深300代理 10 日动量 → 0~1。"""
    out: dict[str, Any] = {
        "score": _NEUTRAL,
        "mom_10": None,
        "source": "benchmark_510300",
        "ok": False,
    }
    try:
        df = load_benchmark(as_of)
        if df is not None and len(df) > 11:
            close = df["close"]
            a, b = float(close.iloc[-1]), float(close.iloc[-11])
            if b > 0:
                mom = a / b - 1.0
                out["mom_10"] = round(mom, 6)
                out["score"] = _clip01((mom + 0.04) / 0.10)
                out["ok"] = True
    except Exception as exc:
        out["error"] = f"{type(exc).__name__}: {exc}"
    return out


def fetch_regime_score(trade_date: str | None = None) -> dict[str, Any]:
    """宏观宽松度近似：LPR 下行 / M2 偏高 → 偏多。失败中性。"""
    day = (trade_date or last_trading_day())[:10]
    cached = _cache_get("regime", day)
    if cached is not None:
        return cached

    out: dict[str, Any] = {
        "score": _NEUTRAL,
        "lpr_1y": None,
        "m2_yoy": None,
        "source": "akshare.macro",
        "ok": False,
    }
    scores: list[float] = []
    try:
        ak = _session_ak()
        try:
            lpr = ak.macro_china_lpr()
            if lpr is not None and not lpr.empty and "LPR1Y" in lpr.columns:
                recent = lpr.tail(3)["LPR1Y"].astype(float)
                out["lpr_1y"] = float(recent.iloc[-1])
                # LPR 下降偏多：对比前值
                if len(recent) >= 2:
                    delta = float(recent.iloc[-2] - recent.iloc[-1])
                    scores.append(_clip01(0.5 + delta * 2.0))  # 25bp → +0.5
                else:
                    # 绝对水平：3% 附近中性，越低越好
                    scores.append(_clip01((4.0 - out["lpr_1y"]) / 2.0))
                out["ok"] = True
        except Exception as exc:
            out["lpr_error"] = str(exc)[:120]

        try:
            m2 = ak.macro_china_money_supply()
            # 表头常含「货币和准货币(M2)-同比增长」
            yoy_col = None
            if m2 is not None and not m2.empty:
                for c in m2.columns:
                    if "M2" in str(c) and "同比" in str(c):
                        yoy_col = c
                        break
            if yoy_col:
                # 新→旧 或 旧→新：取非空最近值
                series = m2[yoy_col].dropna()
                if len(series):
                    # head 更新时取第一条，否则取尾部
                    v = float(series.iloc[0])
                    # 若像旧数据（很大年份在尾），用 head
                    out["m2_yoy"] = v
                    # M2 同比 8% 中性，越高越宽松
                    scores.append(_clip01((v - 4.0) / 8.0))
                    out["ok"] = True
        except Exception as exc:
            out["m2_error"] = str(exc)[:120]
    except Exception as exc:
        out["error"] = f"{type(exc).__name__}: {exc}"

    if scores:
        out["score"] = round(float(np.mean(scores)), 4)
    _cache_set("regime", day, out)
    return out


def get_market_score(as_of: str | None = None) -> dict[str, Any]:
    """市场状态分 = 0.4*北向 + 0.35*指数趋势 + 0.25*宏观 regime。"""
    day = (as_of or last_trading_day())[:10]
    nb = fetch_northbound_net_score(day)
    tr = fetch_benchmark_trend_score(as_of)
    rg = fetch_regime_score(day)
    score = (
        0.40 * float(nb.get("score", _NEUTRAL))
        + 0.35 * float(tr.get("score", _NEUTRAL))
        + 0.25 * float(rg.get("score", _NEUTRAL))
    )
    return _json_safe(
        {
            "score": round(_clip01(score), 4),
            "northbound": nb,
            "trend": tr,
            "regime": rg,
            "trade_date": day,
        }
    )


_GATE_MARKET_SCORE = {
    "aggressive": 0.8,
    "normal": 0.55,
    "defensive": 0.35,
    "risk_off": 0.2,
}


def _regime_gate_level(mkt: dict[str, Any]) -> str | None:
    direct = mkt.get("gate_level")
    if direct:
        return str(direct)
    regime = mkt.get("regime")
    if isinstance(regime, dict) and regime.get("gate_level"):
        return str(regime["gate_level"])
    try:
        from .regime.service import get_current_regime

        current = get_current_regime()
    except Exception:
        return None
    level = current.get("gate_level")
    return str(level) if level else None


def _market_score_for_context(mkt: dict[str, Any]) -> float:
    regime_cfg = load_config().get("regime") or {}
    if regime_cfg.get("use_for_market_score", True):
        level = _regime_gate_level(mkt)
        if level in _GATE_MARKET_SCORE:
            return _GATE_MARKET_SCORE[level]
    return float(mkt.get("score", _NEUTRAL))


# ---------- 行业 ----------


def fetch_industry_strength_map(trade_date: str | None = None) -> dict[str, Any]:
    """行业名 → 强度分 0~1（按涨跌幅排序分位）。"""
    day = (trade_date or last_trading_day())[:10]
    cached = _cache_get("industry_strength", day)
    if cached is not None:
        return cached

    out: dict[str, Any] = {
        "by_name": {},
        "source": "akshare.stock_board_industry_name_em",
        "ok": False,
    }
    try:
        ak = _session_ak()
        df = None
        for caller in (
            lambda: ak.stock_board_industry_name_em(),
            lambda: ak.stock_board_industry_spot_em(),
        ):
            try:
                df = caller()
                if df is not None and not df.empty:
                    break
            except Exception:
                continue
        if df is not None and not df.empty:
            name_col = "板块名称" if "板块名称" in df.columns else df.columns[0]
            pct_col = None
            for c in ("涨跌幅", "涨跌幅%", "涨跌幅％"):
                if c in df.columns:
                    pct_col = c
                    break
            if pct_col is None:
                for c in df.columns:
                    if "涨" in str(c):
                        pct_col = c
                        break
            if pct_col:
                work = df[[name_col, pct_col]].copy()
                work[pct_col] = (
                    work[pct_col]
                    .astype(str)
                    .str.replace("%", "", regex=False)
                    .astype(float)
                )
                ranks = work[pct_col].rank(pct=True)
                by_name = {
                    str(n): round(_clip01(float(r)), 4)
                    for n, r in zip(work[name_col].tolist(), ranks.tolist())
                }
                out["by_name"] = by_name
                out["ok"] = True
    except Exception as exc:
        out["error"] = f"{type(exc).__name__}: {exc}"

    _cache_set("industry_strength", day, out)
    return out


def resolve_symbol_industry(symbol: str) -> str | None:
    """个股所属行业名。失败返回 None。"""
    try:
        sym = normalize_symbol(symbol)
    except ValueError:
        return None
    day = last_trading_day()
    cache_key = f"industry_of:{sym}"
    cached = _cache_get(cache_key, day)
    if cached is not None:
        return cached.get("industry")

    industry: str | None = None
    try:
        ak = _session_ak()
        df = ak.stock_individual_info_em(symbol=sym)
        if df is not None and not df.empty:
            # item / value 列
            cols = list(df.columns)
            if len(cols) >= 2:
                for _, row in df.iterrows():
                    k = str(row.iloc[0])
                    if "行业" in k:
                        industry = str(row.iloc[1]).strip() or None
                        break
    except Exception:
        industry = None

    _cache_set(cache_key, day, {"industry": industry})
    return industry


def sector_score_for_symbol(
    symbol: str, industry_map: dict[str, Any] | None = None
) -> dict[str, Any]:
    """板块分：所属行业强度；ETF/找不到 → 中性。"""
    try:
        sym = normalize_symbol(symbol)
    except ValueError:
        return {"score": _NEUTRAL, "industry": None, "ok": False}

    # ETF：无行业，中性略偏市场
    if sym.startswith(("51", "56", "58", "15", "16", "18")):
        return {"score": _NEUTRAL, "industry": None, "ok": True, "note": "etf"}

    imap = industry_map or fetch_industry_strength_map()
    by_name = imap.get("by_name") or {}
    industry = resolve_symbol_industry(sym)
    if industry and industry in by_name:
        return {
            "score": float(by_name[industry]),
            "industry": industry,
            "ok": True,
        }
    # 模糊匹配
    if industry:
        for k, v in by_name.items():
            if industry in k or k in industry:
                return {"score": float(v), "industry": industry, "matched": k, "ok": True}
    return {"score": _NEUTRAL, "industry": industry, "ok": False}


# ---------- 个股资金 ----------


def fetch_individual_flow_score(symbol: str) -> dict[str, Any]:
    """个股主力净流入近几日 → 0~1。"""
    try:
        sym = normalize_symbol(symbol)
    except ValueError:
        return {"score": _NEUTRAL, "ok": False}

    day = last_trading_day()
    cache_key = f"flow:{sym}"
    cached = _cache_get(cache_key, day)
    if cached is not None:
        return cached

    out: dict[str, Any] = {
        "score": _NEUTRAL,
        "net_inflow": None,
        "source": "akshare.stock_individual_fund_flow",
        "ok": False,
    }
    market = "sh" if sym.startswith(("5", "6", "9")) else "sz"
    try:
        ak = _session_ak()
        df = None
        for caller in (
            lambda: ak.stock_individual_fund_flow(stock=sym, market=market),
            lambda: ak.stock_individual_fund_flow(symbol=sym),
        ):
            try:
                df = caller()
                if df is not None and not df.empty:
                    break
            except TypeError:
                continue
            except Exception:
                continue
        if df is not None and not df.empty:
            col = None
            for c in df.columns:
                if "主力净流入" in str(c) or str(c) in ("主力净流入-净额", "净流入"):
                    col = c
                    break
            if col is None:
                for c in df.columns:
                    if "净流入" in str(c) and "占比" not in str(c):
                        col = c
                        break
            if col:
                vals = []
                for v in df.tail(5)[col].tolist():
                    try:
                        vals.append(float(str(v).replace(",", "")))
                    except (TypeError, ValueError):
                        pass
                if vals:
                    avg = float(np.mean(vals))
                    out["net_inflow"] = round(avg, 2)
                    # 亿级或万元级自适应
                    scale = 5e7 if abs(avg) > 1e6 else 5e3
                    out["score"] = _clip01((avg / scale) + 0.5)
                    out["ok"] = True
    except Exception as exc:
        out["error"] = f"{type(exc).__name__}: {exc}"

    _cache_set(cache_key, day, out)
    return out


# ---------- 估值 ----------


def fetch_valuation_score(symbol: str) -> dict[str, Any]:
    """估值分位：PE 越低分越高。失败中性。ETF → 中性。"""
    try:
        sym = normalize_symbol(symbol)
    except ValueError:
        return {"score": _NEUTRAL, "ok": False}

    if sym.startswith(("51", "56", "58", "15", "16", "18")):
        return {"score": _NEUTRAL, "ok": True, "note": "etf"}

    day = last_trading_day()
    cache_key = f"value:{sym}"
    cached = _cache_get(cache_key, day)
    if cached is not None:
        return cached

    out: dict[str, Any] = {
        "score": _NEUTRAL,
        "pe": None,
        "source": "akshare.stock_zh_valuation_baidu",
        "ok": False,
    }
    try:
        ak = _session_ak()
        df = None
        for indicator in ("市盈率(TTM)", "市盈率"):
            try:
                df = ak.stock_zh_valuation_baidu(
                    symbol=sym, indicator=indicator, period="近一年"
                )
                if df is not None and not df.empty:
                    break
            except Exception:
                continue
        if df is not None and not df.empty:
            # 找数值列
            val_col = None
            for c in df.columns:
                if c not in ("日期", "date", "时间") and df[c].dtype != object:
                    val_col = c
                    break
            if val_col is None:
                for c in df.columns:
                    if c not in ("日期", "date", "时间"):
                        val_col = c
                        break
            if val_col is not None:
                series = (
                    df[val_col]
                    .astype(str)
                    .str.replace(",", "", regex=False)
                    .astype(float)
                    .dropna()
                )
                if len(series) >= 5:
                    last = float(series.iloc[-1])
                    out["pe"] = round(last, 4)
                    # 分位：当前 PE 在近一年中的位置，越低越好
                    pct = float((series <= last).mean())
                    out["pe_percentile"] = round(pct, 4)
                    out["score"] = _clip01(1.0 - pct)
                    out["ok"] = True
    except Exception as exc:
        out["error"] = f"{type(exc).__name__}: {exc}"

    _cache_set(cache_key, day, out)
    return out


def enrich_symbol_context(
    symbol: str,
    *,
    industry_map: dict[str, Any] | None = None,
    market: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """一次性取个股 flow/sector/value + 可选市场分。"""
    flow = fetch_individual_flow_score(symbol)
    sector = sector_score_for_symbol(symbol, industry_map)
    value = fetch_valuation_score(symbol)
    mkt = market or get_market_score()
    return {
        "flow_score": float(flow.get("score", _NEUTRAL)),
        "sector_score": float(sector.get("score", _NEUTRAL)),
        "value_score": float(value.get("score", _NEUTRAL)),
        "market_score": _market_score_for_context(mkt),
        "flow": flow,
        "sector": sector,
        "value": value,
        "market": mkt,
    }
