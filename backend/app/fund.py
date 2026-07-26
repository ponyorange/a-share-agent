"""Open-ended (场外) fund search and detail via AKShare."""

from __future__ import annotations

import time
from typing import Any

import akshare as ak
import pandas as pd

FUND_NAME_CACHE_TTL_SEC = 3600

_name_cache: list[dict[str, str]] | None = None
_name_cache_at: float = 0.0


def clear_fund_name_cache() -> None:
    global _name_cache, _name_cache_at
    _name_cache = None
    _name_cache_at = 0.0


def _load_name_rows() -> list[dict[str, str]]:
    global _name_cache, _name_cache_at
    now = time.monotonic()
    if _name_cache is not None and (now - _name_cache_at) < FUND_NAME_CACHE_TTL_SEC:
        return _name_cache
    df = ak.fund_name_em()
    rows: list[dict[str, str]] = []
    if df is None or df.empty:
        _name_cache = rows
        _name_cache_at = now
        return rows
    for _, r in df.iterrows():
        symbol = str(r.get("基金代码") or "").strip()
        if not symbol:
            continue
        rows.append(
            {
                "symbol": symbol,
                "name": str(r.get("基金简称") or "").strip(),
                "type": str(r.get("基金类型") or "").strip(),
                "pinyin": str(r.get("拼音缩写") or "").strip(),
            }
        )
    _name_cache = rows
    _name_cache_at = now
    return rows


def search_funds(q: str, limit: int = 20) -> list[dict[str, Any]]:
    query = (q or "").strip()
    if not query:
        return []
    lim = max(1, min(int(limit or 20), 50))
    needle = query.casefold()
    out: list[dict[str, Any]] = []
    for row in _load_name_rows():
        symbol = row["symbol"]
        name = row["name"]
        pinyin = row["pinyin"]
        if (
            symbol.startswith(query)
            or needle in name.casefold()
            or pinyin.casefold().startswith(needle)
        ):
            out.append(
                {
                    "symbol": symbol,
                    "name": name,
                    "type": row["type"],
                    "pinyin": pinyin,
                }
            )
            if len(out) >= lim:
                break
    return out


def _normalize_symbol(symbol: str) -> str:
    clean = "".join(ch for ch in str(symbol or "") if ch.isdigit())
    if len(clean) != 6:
        raise ValueError("基金代码须为 6 位数字")
    return clean


def _cell(row: pd.Series, key: str) -> str:
    if key not in row.index:
        return ""
    v = row[key]
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    return str(v).strip()


def _num(v: Any) -> float | None:
    if v is None or v == "" or v == "-":
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f:
        return None
    return f


def _parse_overview(df: pd.DataFrame) -> tuple[str, dict[str, Any]] | None:
    if df is None or df.empty:
        return None
    row = df.iloc[0]
    name = _cell(row, "基金简称")
    overview = {
        "full_name": _cell(row, "基金全称"),
        "type": _cell(row, "基金类型"),
        "establish_date": _cell(row, "成立日期/规模"),
        "scale": _cell(row, "净资产规模"),
        "manager": _cell(row, "基金经理人"),
        "company": _cell(row, "基金管理人"),
        "custodian": _cell(row, "基金托管人"),
        "benchmark": _cell(row, "业绩比较基准"),
        "tracking": _cell(row, "跟踪标的"),
        "fees": {
            "management": _cell(row, "管理费率"),
            "custody": _cell(row, "托管费率"),
            "sales": _cell(row, "销售服务费率"),
            "subscribe": _cell(row, "最高申购费率") or _cell(row, "最高认购费率"),
            "redeem": _cell(row, "最高赎回费率"),
        },
    }
    return name, overview


def _parse_nav(df: pd.DataFrame) -> dict[str, Any] | None:
    if df is None or df.empty:
        return None
    series: list[dict[str, Any]] = []
    for _, r in df.iterrows():
        date = _cell(r, "净值日期")
        nav = _num(r["单位净值"] if "单位净值" in r.index else None)
        if not date or nav is None:
            continue
        chg = _num(r["日增长率"] if "日增长率" in r.index else None)
        series.append({"date": date, "nav": nav, "change_pct": chg})
    if not series:
        return None
    latest = series[-1]
    return {"latest": latest, "series": series}


def get_fund_detail(symbol: str) -> dict[str, Any]:
    sym = _normalize_symbol(symbol)
    overview_name = ""
    overview: dict[str, Any] | None = None
    try:
        ov_df = ak.fund_overview_em(symbol=sym)
        parsed = _parse_overview(ov_df)
        if parsed:
            overview_name, overview = parsed
    except Exception:
        overview = None

    nav: dict[str, Any] | None = None
    nav_error: str | None = None
    try:
        nav_df = ak.fund_open_fund_info_em(
            symbol=sym, indicator="单位净值走势", period="成立来"
        )
        nav = _parse_nav(nav_df)
        if nav is None:
            nav_error = "净值数据为空"
    except Exception as exc:
        nav = None
        nav_error = f"{type(exc).__name__}: {exc}"

    if overview is None and nav is None:
        raise LookupError(f"未找到基金 {sym}")

    name = overview_name or sym
    out: dict[str, Any] = {
        "symbol": sym,
        "name": name,
        "overview": overview,
        "nav": nav,
    }
    if nav is None and nav_error:
        out["nav_error"] = nav_error
    return out
