"""Dynamic candidate universe via AKShare (+ Eastmoney fallback).

Boards:
- etf: 场内 ETF（fund_etf_spot_em）
- hs: 沪深股（上证主板/深市主板/创业板等，不含科创板）
- star: 科创板（688）
"""

from __future__ import annotations

import time
from typing import Any, Literal

import akshare as ak
import requests

from .config_loader import load_config
from .portfolio import load_portfolio

BoardId = Literal["etf", "hs", "star"]

BOARD_LABELS: dict[BoardId, str] = {
    "etf": "ETF",
    "hs": "沪深股",
    "star": "科创股",
}

BENCHMARK_SYMBOL = "510300"

# 仅作 AKShare 失败时的兜底，不再作为主候选池
_FALLBACK_ETF: list[dict[str, Any]] = [
    {"symbol": "510300", "name": "沪深300ETF", "amount": 0.0},
    {"symbol": "510500", "name": "中证500ETF", "amount": 0.0},
    {"symbol": "159915", "name": "创业板ETF", "amount": 0.0},
    {"symbol": "588000", "name": "科创50ETF", "amount": 0.0},
    {"symbol": "512480", "name": "半导体ETF", "amount": 0.0},
]

_cache: dict[str, Any] = {"ts": 0.0, "data": None}


def classify_symbol(symbol: str) -> BoardId | None:
    """Map 6-digit code to board. Returns None if unknown / skip."""
    s = "".join(ch for ch in str(symbol).strip() if ch.isdigit())
    if len(s) != 6:
        return None
    # ETF / 场内基金常见号段
    if s.startswith(("51", "56", "58", "15", "16", "18")):
        return "etf"
    # 科创板
    if s.startswith("688"):
        return "star"
    # 沪市主板 / 深市主板中小创（不含 688）
    if s.startswith(("60", "00", "001", "002", "003", "30")):
        return "hs"
    return None


def _session() -> requests.Session:
    s = requests.Session()
    s.trust_env = False
    s.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
            "Referer": "https://quote.eastmoney.com/",
        }
    )
    return s


def _num(v: Any) -> float:
    try:
        f = float(v)
        return 0.0 if f != f else f
    except (TypeError, ValueError):
        return 0.0


def _is_st(name: str) -> bool:
    n = name.upper()
    return "ST" in n or "退" in name


def _is_money_market_etf(name: str) -> bool:
    """货币/日利等不适合次日涨跌精算。"""
    keys = ("货币", "日利", "快线", "添益", "理财", "债", "国债", "政金债", "城投")
    return any(k in name for k in keys)

def _row(
    symbol: str,
    name: str,
    amount: float,
    board: BoardId,
    *,
    pct_chg: float | None = None,
    volume_ratio: float | None = None,
    turnover: float | None = None,
    price: float | None = None,
) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "name": name or symbol,
        "amount": float(amount),
        "board": board,
        "pct_chg": pct_chg,
        "volume_ratio": volume_ratio,
        "turnover": turnover,
        "price": price,
    }


def _opt_num(v: Any) -> float | None:
    try:
        if v is None or v == "" or v == "-":
            return None
        f = float(v)
        return None if f != f else f
    except (TypeError, ValueError):
        return None


def _fetch_etf_akshare(limit: int) -> tuple[list[dict[str, Any]], str]:
    df = ak.fund_etf_spot_em()
    if df is None or df.empty:
        raise RuntimeError("fund_etf_spot_em empty")
    code_col = "代码" if "代码" in df.columns else df.columns[0]
    name_col = "名称" if "名称" in df.columns else df.columns[1]
    work = df.copy()
    work["_code"] = work[code_col].astype(str).str.zfill(6)
    work["_name"] = work[name_col].astype(str)
    work["_amt"] = work["成交额"].map(_num) if "成交额" in work.columns else 0.0
    work = work.sort_values("_amt", ascending=False)
    out: list[dict[str, Any]] = []
    for _, r in work.iterrows():
        sym = str(r["_code"])
        if classify_symbol(sym) != "etf":
            continue
        if _is_st(str(r["_name"])):
            continue
        if _is_money_market_etf(str(r["_name"])):
            continue
        out.append(
            _row(
                sym,
                str(r["_name"]),
                float(r["_amt"]),
                "etf",
                pct_chg=_opt_num(r["涨跌幅"]) if "涨跌幅" in work.columns else None,
                volume_ratio=_opt_num(r["量比"]) if "量比" in work.columns else None,
                turnover=_opt_num(r["换手率"]) if "换手率" in work.columns else None,
                price=_opt_num(r["最新价"]) if "最新价" in work.columns else None,
            )
        )
        if len(out) >= limit:
            break
    if not out:
        raise RuntimeError("no etf after filter")
    return out, "akshare.fund_etf_spot_em"


def _fetch_stocks_akshare(
    limit_hs: int, limit_star: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
    df = ak.stock_zh_a_spot_em()
    if df is None or df.empty:
        raise RuntimeError("stock_zh_a_spot_em empty")
    code_col = "代码" if "代码" in df.columns else df.columns[0]
    name_col = "名称" if "名称" in df.columns else df.columns[1]
    work = df.copy()
    work["_code"] = work[code_col].astype(str).str.zfill(6)
    work["_name"] = work[name_col].astype(str)
    work["_amt"] = work["成交额"].map(_num) if "成交额" in work.columns else 0.0
    work = work.sort_values("_amt", ascending=False)

    hs: list[dict[str, Any]] = []
    star: list[dict[str, Any]] = []
    for _, r in work.iterrows():
        sym = str(r["_code"])
        name = str(r["_name"])
        if _is_st(name):
            continue
        board = classify_symbol(sym)
        item = _row(
            sym,
            name,
            float(r["_amt"]),
            board or "hs",
            pct_chg=_opt_num(r["涨跌幅"]) if "涨跌幅" in work.columns else None,
            volume_ratio=_opt_num(r["量比"]) if "量比" in work.columns else None,
            turnover=_opt_num(r["换手率"]) if "换手率" in work.columns else None,
            price=_opt_num(r["最新价"]) if "最新价" in work.columns else None,
        )
        if board == "hs" and len(hs) < limit_hs:
            hs.append(item)
        elif board == "star" and len(star) < limit_star:
            star.append(item)
        if len(hs) >= limit_hs and len(star) >= limit_star:
            break
    if not hs and not star:
        raise RuntimeError("no stocks after filter")
    return hs, star, "akshare.stock_zh_a_spot_em"


def _fetch_clist_board(fs: str, board: BoardId, limit: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    pn = 1
    while len(out) < limit and pn <= 10:
        need = limit - len(out)
        pz = min(100, max(need + 10, 20))
        r = _session().get(
            "https://push2delay.eastmoney.com/api/qt/clist/get",
            params={
                "pn": str(pn),
                "pz": str(pz),
                "po": "1",
                "np": "1",
                "fltt": "2",
                "invt": "2",
                "fid": "f6",
                "fs": fs,
                "fields": "f12,f14,f2,f3,f6,f8,f10",
            },
            timeout=20,
        )
        r.raise_for_status()
        diff = (r.json().get("data") or {}).get("diff") or []
        if not diff:
            break
        for row in diff:
            sym = str(row.get("f12") or "").zfill(6)
            name = str(row.get("f14") or sym)
            if _is_st(name):
                continue
            if classify_symbol(sym) != board:
                continue
            out.append(
                _row(
                    sym,
                    name,
                    _num(row.get("f6")),
                    board,
                    pct_chg=_opt_num(row.get("f3")),
                    volume_ratio=_opt_num(row.get("f10")),
                    turnover=_opt_num(row.get("f8")),
                    price=_opt_num(row.get("f2")),
                )
            )
            if len(out) >= limit:
                break
        pn += 1
    return out


def _fetch_stocks_eastmoney(
    limit_hs: int, limit_star: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
    # 沪深：沪主板 + 深主板 + 创业板（不含科创）
    hs_fs = "m:1+t:2,m:0+t:6,m:0+t:80"
    star_fs = "m:1+t:23"
    hs = _fetch_clist_board(hs_fs, "hs", limit_hs)
    star = _fetch_clist_board(star_fs, "star", limit_star)
    if not hs and not star:
        raise RuntimeError("eastmoney clist empty")
    return hs, star, "eastmoney.clist"


def _merge_portfolio(
    pool: list[dict[str, Any]], board: BoardId
) -> list[dict[str, Any]]:
    by_sym = {p["symbol"]: dict(p) for p in pool}
    for pos in load_portfolio().get("positions") or []:
        sym = str(pos.get("symbol") or "")
        if classify_symbol(sym) != board:
            continue
        if sym not in by_sym:
            by_sym[sym] = _row(sym, str(pos.get("name") or sym), 0.0, board)
        else:
            # keep name from portfolio if present
            if pos.get("name"):
                by_sym[sym]["name"] = pos["name"]
    return list(by_sym.values())


def _pool_limits() -> dict[str, int]:
    cfg = load_config()
    rec = cfg.get("recommendations") or {}
    pool = rec.get("pool") or {}
    return {
        "etf": int(pool.get("etf", 200)),
        "hs": int(pool.get("hs", 250)),
        "star": int(pool.get("star", 150)),
    }


def precise_limits() -> dict[str, int]:
    cfg = load_config()
    rec = cfg.get("recommendations") or {}
    precise = rec.get("precise") or {}
    return {
        "etf": int(precise.get("etf", 25)),
        "hs": int(precise.get("hs", 30)),
        "star": int(precise.get("star", 20)),
    }


def build_universe(force: bool = False) -> dict[str, Any]:
    """Build per-board candidate pools (amount-ranked) with portfolio merge."""
    result = None
    for ev in iter_build_universe_events(force=force):
        if ev["event"] == "done":
            result = ev["data"]
        elif ev["event"] == "error":
            raise RuntimeError(ev["data"].get("detail") or "候选池构建失败")
    if result is None:
        raise RuntimeError("候选池构建未完成")
    return result


def iter_build_universe_events(force: bool = False):
    """SSE 友好：progress(universe) → done(universe dict)。命中缓存直接 done。"""
    cfg = load_config()
    rec = cfg.get("recommendations") or {}
    ttl = float(rec.get("universe_cache_ttl_seconds", 1800))
    now = time.time()
    if (
        not force
        and _cache["data"] is not None
        and now - float(_cache["ts"]) < ttl
    ):
        yield {
            "event": "progress",
            "data": {
                "phase": "universe",
                "step": "cache",
                "message": "候选池缓存命中",
                "done": 1,
                "total": 1,
            },
        }
        yield {"event": "done", "data": _cache["data"]}
        return

    limits = _pool_limits()
    sources: list[str] = []
    boards: dict[str, list[dict[str, Any]]] = {
        "etf": [],
        "hs": [],
        "star": [],
    }

    yield {
        "event": "progress",
        "data": {
            "phase": "universe",
            "step": "etf",
            "message": "拉取 ETF 候选池…",
            "done": 0,
            "total": 2,
        },
    }
    try:
        etf, src = _fetch_etf_akshare(limits["etf"])
        boards["etf"] = etf
        sources.append(src)
    except Exception as exc:
        boards["etf"] = list(_FALLBACK_ETF)[: limits["etf"]]
        sources.append(f"fallback.etf({exc})")

    yield {
        "event": "progress",
        "data": {
            "phase": "universe",
            "step": "etf_done",
            "message": f"ETF 候选 {len(boards['etf'])} 只",
            "done": 1,
            "total": 2,
            "count": len(boards["etf"]),
        },
    }

    yield {
        "event": "progress",
        "data": {
            "phase": "universe",
            "step": "hs",
            "message": "拉取沪深/科创候选池…",
            "done": 1,
            "total": 2,
        },
    }
    try:
        hs, star, src = _fetch_stocks_akshare(limits["hs"], limits["star"])
        boards["hs"] = hs
        boards["star"] = star
        sources.append(src)
    except Exception as exc1:
        try:
            hs, star, src = _fetch_stocks_eastmoney(limits["hs"], limits["star"])
            boards["hs"] = hs
            boards["star"] = star
            sources.append(f"{src};akshare_failed={exc1}")
        except Exception as exc2:
            boards["hs"] = []
            boards["star"] = []
            sources.append(f"stocks_failed ak={exc1}; em={exc2}")

    yield {
        "event": "progress",
        "data": {
            "phase": "universe",
            "step": "hs_done",
            "message": f"沪深 {len(boards['hs'])} / 科创 {len(boards['star'])}",
            "done": 2,
            "total": 2,
            "count_hs": len(boards["hs"]),
            "count_star": len(boards["star"]),
        },
    }

    for board_id in ("etf", "hs", "star"):
        boards[board_id] = _merge_portfolio(boards[board_id], board_id)  # type: ignore[arg-type]

    result = {
        "as_of": time.strftime("%Y-%m-%d %H:%M:%S"),
        "benchmark": BENCHMARK_SYMBOL,
        "source": " | ".join(sources),
        "boards": {
            bid: {
                "id": bid,
                "label": BOARD_LABELS[bid],  # type: ignore[index]
                "count": len(boards[bid]),
                "symbols": boards[bid],
            }
            for bid in ("etf", "hs", "star")
        },
    }
    _cache["ts"] = now
    _cache["data"] = result
    yield {"event": "done", "data": result}


def list_board_candidates(board: BoardId, force: bool = False) -> list[dict[str, Any]]:
    data = build_universe(force=force)
    block = (data.get("boards") or {}).get(board) or {}
    return list(block.get("symbols") or [])


def peek_board_candidates(board: BoardId) -> list[dict[str, Any]]:
    """只读内存缓存，绝不触发 AKShare 拉池。缓存未命中返回空列表。"""
    data = _cache.get("data")
    if not data:
        return []
    block = (data.get("boards") or {}).get(board) or {}
    return list(block.get("symbols") or [])


def fallback_etf_symbols(limit: int = 12) -> list[str]:
    return [u["symbol"] for u in _FALLBACK_ETF[:limit]]

def list_universe(extra_symbols: list[str] | None = None) -> list[dict[str, str]]:
    """Flat list across boards (legacy helpers / backtest)."""
    build_universe()
    by_sym: dict[str, dict[str, str]] = {}
    for bid in ("etf", "hs", "star"):
        for u in list_board_candidates(bid):  # type: ignore[arg-type]
            by_sym[u["symbol"]] = {"symbol": u["symbol"], "name": u["name"]}
    for raw in extra_symbols or []:
        sym = "".join(ch for ch in str(raw).strip() if ch.isdigit())
        if len(sym) != 6:
            continue
        if sym not in by_sym:
            by_sym[sym] = {"symbol": sym, "name": sym}
    return list(by_sym.values())


def universe_symbols(extra_symbols: list[str] | None = None) -> list[str]:
    return [u["symbol"] for u in list_universe(extra_symbols)]


def name_for(symbol: str) -> str:
    for u in list_universe():
        if u["symbol"] == symbol:
            return u["name"]
    return symbol


def describe_universe() -> dict[str, Any]:
    data = build_universe()
    return {
        "benchmark": data.get("benchmark"),
        "source": data.get("source"),
        "as_of": data.get("as_of"),
        "boards": {
            bid: {
                "label": BOARD_LABELS[bid],  # type: ignore[index]
                "count": block["count"],
                "symbols": [s["symbol"] for s in block["symbols"]],
            }
            for bid, block in (data.get("boards") or {}).items()
        },
    }
