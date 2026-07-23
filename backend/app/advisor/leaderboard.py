"""龙虎榜：涨跌幅榜 + 资金流入流出榜（ETF / 沪深 / 科创）。"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Literal

from ..db import get_db
from .calendar_util import last_trading_day
from .universe import (
    BOARD_LABELS,
    BoardId,
    _is_money_market_etf,
    _is_st,
    _num,
    _opt_num,
    _session,
    classify_symbol,
)

ListId = Literal["gainers", "losers", "inflow", "outflow"]

LIST_LABELS: dict[ListId, str] = {
    "gainers": "涨幅榜",
    "losers": "跌幅榜",
    "inflow": "资金流入榜",
    "outflow": "资金流出榜",
}

# 东财 clist 板块过滤
_BOARD_FS: dict[BoardId, str] = {
    # 场内基金 / ETF
    "etf": "b:MK0021,b:MK0022,b:MK0023,b:MK0024",
    # 沪主板 + 深主板 + 创业板（不含科创）
    "hs": "m:1+t:2,m:0+t:6,m:0+t:80",
    # 科创板
    "star": "m:1+t:23",
}

# fid: f3 涨跌幅；f62 主力净流入。po: 1 降序，0 升序
_LIST_SORT: dict[ListId, tuple[str, str]] = {
    "gainers": ("f3", "1"),
    "losers": ("f3", "0"),
    "inflow": ("f62", "1"),
    "outflow": ("f62", "0"),
}

TOP_N = 25
_FETCH_PAGE = 50  # 多取再过滤


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")


def _item_from_row(row: dict[str, Any], board: BoardId) -> dict[str, Any] | None:
    sym = str(row.get("f12") or "").zfill(6)
    name = str(row.get("f14") or sym)
    if classify_symbol(sym) != board:
        return None
    if _is_st(name):
        return None
    if board == "etf" and _is_money_market_etf(name):
        return None
    pct = _opt_num(row.get("f3"))
    inflow = _opt_num(row.get("f62"))
    return {
        "symbol": sym,
        "name": name,
        "board": board,
        "price": _opt_num(row.get("f2")),
        "pct_chg": pct,
        "amount": _num(row.get("f6")),
        "main_net_inflow": inflow,
        "main_net_inflow_pct": _opt_num(row.get("f184")),
    }


def _fetch_board_list(
    board: BoardId,
    list_id: ListId,
    *,
    top: int = TOP_N,
) -> list[dict[str, Any]]:
    fid, po = _LIST_SORT[list_id]
    fs = _BOARD_FS[board]
    r = _session().get(
        "https://push2delay.eastmoney.com/api/qt/clist/get",
        params={
            "pn": "1",
            "pz": str(max(_FETCH_PAGE, top * 3)),
            "po": po,
            "np": "1",
            "fltt": "2",
            "invt": "2",
            "fid": fid,
            "fs": fs,
            "fields": "f12,f14,f2,f3,f6,f62,f184",
        },
        timeout=25,
    )
    r.raise_for_status()
    diff = (r.json().get("data") or {}).get("diff") or []
    out: list[dict[str, Any]] = []
    for row in diff:
        item = _item_from_row(row, board)
        if not item:
            continue
        # 跌幅榜：只要下跌；流出榜：只要净流出（负值）
        if list_id == "losers" and (item.get("pct_chg") is None or item["pct_chg"] >= 0):
            continue
        if list_id == "gainers" and (item.get("pct_chg") is None or item["pct_chg"] <= 0):
            continue
        if list_id == "outflow" and (
            item.get("main_net_inflow") is None or item["main_net_inflow"] >= 0
        ):
            continue
        if list_id == "inflow" and (
            item.get("main_net_inflow") is None or item["main_net_inflow"] <= 0
        ):
            continue
        out.append(item)
        if len(out) >= top:
            break
    return out


def load_leaderboard(trade_date: str | None = None) -> dict[str, Any] | None:
    day = trade_date or last_trading_day()
    doc = get_db().leaderboard_snapshots.find_one({"trade_date": day}, {"_id": 0})
    return doc


def save_leaderboard(payload: dict[str, Any]) -> dict[str, Any]:
    day = str(payload.get("trade_date") or last_trading_day())
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    doc = {
        **payload,
        "trade_date": day,
        "updated_at": now,
    }
    get_db().leaderboard_snapshots.update_one(
        {"trade_date": day},
        {"$set": doc, "$setOnInsert": {"created_at": now}},
        upsert=True,
    )
    return doc


def empty_boards() -> dict[str, Any]:
    return {
        lid: {bid: [] for bid in ("etf", "hs", "star")}
        for lid in ("gainers", "losers", "inflow", "outflow")
    }


def iter_leaderboard_events(*, force: bool = False, trade_date: str | None = None):
    """SSE：meta → progress* → done。force=False 且有当日缓存则直接 done。"""
    day = trade_date or last_trading_day()
    if not force:
        cached = load_leaderboard(day)
        if cached and cached.get("boards"):
            yield {
                "event": "meta",
                "data": {
                    "total": 0,
                    "cached": True,
                    "trade_date": day,
                    "phase": "cache",
                },
            }
            yield {
                "event": "done",
                "data": {**cached, "from_cache": True},
            }
            return

    boards_order: list[BoardId] = ["etf", "hs", "star"]
    lists_order: list[ListId] = ["gainers", "losers", "inflow", "outflow"]
    steps = [(lid, bid) for lid in lists_order for bid in boards_order]
    total = len(steps)

    yield {
        "event": "meta",
        "data": {
            "total": total,
            "cached": False,
            "trade_date": day,
            "phase": "fetch",
            "top": TOP_N,
        },
    }

    boards = empty_boards()
    errors: list[dict[str, str]] = []
    done = 0
    for list_id, board in steps:
        label = f"{LIST_LABELS[list_id]} · {BOARD_LABELS[board]}"
        try:
            items = _fetch_board_list(board, list_id, top=TOP_N)
            boards[list_id][board] = items
            ok = True
            detail = f"{len(items)} 条"
        except Exception as exc:
            boards[list_id][board] = []
            ok = False
            detail = f"{type(exc).__name__}: {exc}"
            errors.append({"list": list_id, "board": board, "detail": detail})
        done += 1
        yield {
            "event": "progress",
            "data": {
                "done": done,
                "total": total,
                "list_id": list_id,
                "list_label": LIST_LABELS[list_id],
                "board": board,
                "board_label": BOARD_LABELS[board],
                "label": label,
                "ok": ok,
                "detail": detail,
                "count": len(boards[list_id][board]),
            },
        }

    payload = {
        "trade_date": day,
        "as_of": _now_iso(),
        "source": "eastmoney.clist",
        "top": TOP_N,
        "boards": boards,
        "list_labels": LIST_LABELS,
        "board_labels": BOARD_LABELS,
        "errors": errors,
        "from_cache": False,
    }
    saved = save_leaderboard(payload)
    yield {"event": "done", "data": {**saved, "from_cache": False}}
