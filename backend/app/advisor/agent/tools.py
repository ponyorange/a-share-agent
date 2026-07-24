"""Agent tools bound to a specific user_id."""

from __future__ import annotations

import json
from typing import Any

from langchain_core.tools import tool

from .. import context
from ..leaderboard import LIST_LABELS, load_leaderboard
from ..llm_settings import public_llm_settings
from ..paper import (
    PaperOrderBody,
    delete_position,
    get_account,
    list_trades,
    paper_pnl_summary,
    place_order,
    reset_account,
    sell_all_positions,
)
from ..portfolio import (
    PortfolioPayload,
    load_portfolio,
    remove_position,
    save_portfolio,
    upsert_position,
)
from ..service import get_advice, get_portfolio_advice
from ..snapshots import (
    effective_rec_date,
    has_snapshot,
    list_snapshot_dates,
    snapshot_as_recommendations,
)
from ..user_strategy import (
    get_user_strategy,
    strategy_public_view,
    update_user_strategy,
)
from . import unstructured as ustr
from .data_agent.delegate import build_delegate_data_tool


def _need_confirm(action: str, preview: dict[str, Any]) -> str:
    return json.dumps(
        {
            "applied": False,
            "needs_confirm": True,
            "action": action,
            "preview": preview,
            "message": "未确认。请向用户复述拟执行内容，用户明确同意后再以 confirm=true 调用。",
        },
        ensure_ascii=False,
        default=str,
    )


def _fmt_ratio_pct(v: Any, digits: int = 2) -> str | None:
    """Format decimal ratio (0.19 = 19%) for agent display. Never treat as already-%."""
    if v is None:
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    if x != x:  # NaN
        return None
    sign = "+" if x > 0 else ""
    return f"{sign}{x * 100:.{digits}f}%"


def _slim_rec_items(items: list[dict[str, Any]], limit: int = 12) -> list[dict[str, Any]]:
    out = []
    for it in items[:limit]:
        layers = it.get("layer_scores") or {}
        day_chg = it.get("day_chg_pct")
        row: dict[str, Any] = {
            "symbol": it.get("symbol"),
            "name": it.get("name"),
            "score": it.get("score"),
            "action": it.get("action"),
            "action_label": it.get("action_label"),
            "board": it.get("board"),
            "industry": it.get("industry"),
            "day_chg_pct": day_chg,
            "day_chg": _fmt_ratio_pct(day_chg),
            "close": it.get("close"),
            "rationale": (it.get("rationale") or "")[:200],
        }
        if layers:
            row["layer_scores"] = {
                k: layers.get(k)
                for k in (
                    "tech_score",
                    "flow_score",
                    "sector_score",
                    "value_score",
                    "market_score",
                )
                if layers.get(k) is not None
            }
        out.append(row)
    return out


def build_tools(user_id: str) -> list[Any]:
    """Create tools closed over user_id (and re-bind context on each call)."""

    def _bind() -> None:
        context.bind_user(user_id)

    @tool
    def get_today_recommendations(board: str = "all") -> str:
        """获取当前用户「今日关注」多因子推荐列表摘要（综合分 + tech/flow/sector/value/market 子分）。
        用户问今日关注/今日推荐时优先调用。board 可选 etf/hs/star/all。
        无归档时提示去基础面板刷新候选池；可再配合联播/宏观工具补充叙事。
        """
        _bind()
        td = effective_rec_date()
        if not has_snapshot(td, user_id=user_id):
            return json.dumps(
                {
                    "trade_date": td,
                    "message": "暂无今日归档，请先在基础面板「今日关注」点击刷新候选池",
                },
                ensure_ascii=False,
            )
        board_key = None if board in ("", "all") else board
        recs = snapshot_as_recommendations(td, board=board_key, user_id=user_id)
        if not recs:
            return json.dumps({"trade_date": td, "message": "无推荐数据"}, ensure_ascii=False)
        boards = recs.get("boards") or {}
        summary = {
            "trade_date": td,
            "buy_threshold": recs.get("buy_threshold"),
            "boards": {
                bid: {
                    "count": block.get("count"),
                    "items": _slim_rec_items(block.get("items") or []),
                }
                for bid, block in boards.items()
            },
        }
        return json.dumps(summary, ensure_ascii=False, default=str)

    @tool
    def get_portfolio_summary() -> str:
        """获取用户真实持仓列表摘要。"""
        _bind()
        port = load_portfolio(user_id)
        positions = port.get("positions") or []
        slim = [
            {
                "symbol": p.get("symbol"),
                "name": p.get("name"),
                "qty": p.get("qty"),
                "cost": p.get("cost"),
                "note": p.get("note"),
            }
            for p in positions
        ]
        return json.dumps(
            {"count": len(slim), "positions": slim},
            ensure_ascii=False,
            default=str,
        )

    @tool
    def get_paper_pnl_brief() -> str:
        """获取模拟盘现金、市值与收益摘要。"""
        _bind()
        acc = get_account(user_id, mark_to_market=False)
        pnl = paper_pnl_summary(user_id)
        return json.dumps(
            {
                "cash": acc.get("cash"),
                "equity": acc.get("equity"),
                "market_value": acc.get("market_value"),
                "positions_count": len(acc.get("positions") or []),
                "historical_total_pnl": (pnl.get("historical") or {})
                .get("total", {})
                .get("pnl"),
                "holding_total_pnl": (pnl.get("holding") or {})
                .get("total", {})
                .get("pnl"),
            },
            ensure_ascii=False,
            default=str,
        )

    @tool
    def get_symbol_advice(symbol: str) -> str:
        """对单个股票/ETF 做规则诊断摘要。symbol 为 6 位代码。
        day_chg_pct 为小数比例（0.19=涨19%）；对用户展示涨跌幅时必须用 day_chg 字段，勿把 0.19 写成 0.19%。
        """
        _bind()
        try:
            row = get_advice(symbol=symbol.strip())
        except Exception as exc:
            return json.dumps(
                {"symbol": symbol, "error": f"{type(exc).__name__}: {exc}"},
                ensure_ascii=False,
            )
        day_chg = row.get("day_chg_pct")
        return json.dumps(
            {
                "symbol": row.get("symbol"),
                "name": row.get("name"),
                "score": row.get("score"),
                "action": row.get("action"),
                "action_label": row.get("action_label"),
                "rationale": (row.get("rationale") or "")[:400],
                "close": row.get("close"),
                "prev_close": row.get("prev_close"),
                "day_chg_pct": day_chg,
                "day_chg": _fmt_ratio_pct(day_chg),
                "pct_unit": "day_chg_pct 是小数比例(0.01=1%)，展示用 day_chg",
            },
            ensure_ascii=False,
            default=str,
        )

    @tool
    def get_user_strategy_config() -> str:
        """读取当前用户策略：买入/加仓/卖出阈值与因子权重。"""
        _bind()
        view = strategy_public_view(get_user_strategy(user_id))
        cfg = view.get("config") or {}
        return json.dumps(
            {
                "source": view.get("source"),
                "version": view.get("version"),
                "buy_threshold": cfg.get("buy_threshold"),
                "add_threshold": cfg.get("add_threshold"),
                "sell_threshold": cfg.get("sell_threshold"),
                "layer_weights": cfg.get("layer_weights"),
                "market_scale": cfg.get("market_scale"),
                "weights": cfg.get("weights"),
                "notes": view.get("notes"),
            },
            ensure_ascii=False,
            default=str,
        )

    @tool
    def propose_strategy_patch(instruction: str) -> str:
        """根据用户自然语言意图，提出策略 config_patch 草案（不落库）。
        返回建议的阈值/权重修改说明与 JSON patch，供用户确认后再 apply。
        """
        _bind()
        view = strategy_public_view(get_user_strategy(user_id))
        cfg = view.get("config") or {}
        # 启发式草案：实际精细解读由 LLM 在工具外完成；这里提供当前基线
        return json.dumps(
            {
                "message": (
                    "请根据用户意图 instruction 与当前配置，在最终回复中给出明确的 "
                    "config_patch（可含 buy_threshold/add_threshold/sell_threshold/"
                    "layer_weights/market_scale/weights），"
                    "并提醒用户确认后调用 apply_strategy_patch(confirm=true)。"
                ),
                "instruction": instruction,
                "current": {
                    "buy_threshold": cfg.get("buy_threshold"),
                    "add_threshold": cfg.get("add_threshold"),
                    "sell_threshold": cfg.get("sell_threshold"),
                    "layer_weights": cfg.get("layer_weights"),
                    "market_scale": cfg.get("market_scale"),
                    "weights": cfg.get("weights"),
                    "version": view.get("version"),
                },
            },
            ensure_ascii=False,
            default=str,
        )

    @tool
    def apply_strategy_patch(config_patch_json: str, confirm: bool = False) -> str:
        """将策略补丁写入用户配置。必须 confirm=true 才会落库。
        config_patch_json 为 JSON 字符串，可含 buy_threshold/add_threshold/sell_threshold/
        layer_weights/market_scale/weights。
        """
        _bind()
        if not confirm:
            return json.dumps(
                {
                    "applied": False,
                    "message": "未确认。请向用户展示 patch 后，再以 confirm=true 调用。",
                },
                ensure_ascii=False,
            )
        try:
            patch = json.loads(config_patch_json)
        except json.JSONDecodeError as exc:
            return json.dumps(
                {"applied": False, "error": f"JSON 解析失败: {exc}"},
                ensure_ascii=False,
            )
        if not isinstance(patch, dict) or not patch:
            return json.dumps(
                {"applied": False, "error": "config_patch 必须是非空对象"},
                ensure_ascii=False,
            )
        allowed = {
            "buy_threshold",
            "add_threshold",
            "sell_threshold",
            "layer_weights",
            "market_scale",
            "weights",
            "high_vol_penalty",
            "high_vol_ann_threshold",
        }
        clean = {k: v for k, v in patch.items() if k in allowed}
        if not clean:
            return json.dumps(
                {"applied": False, "error": f"无允许字段，允许: {sorted(allowed)}"},
                ensure_ascii=False,
            )
        doc = update_user_strategy(
            user_id,
            config_patch=clean,
            source="agent",
            notes=f"Agent 应用补丁: {list(clean.keys())}",
        )
        view = strategy_public_view(doc)
        return json.dumps(
            {
                "applied": True,
                "version": view.get("version"),
                "source": view.get("source"),
                "config": {
                    "buy_threshold": (view.get("config") or {}).get("buy_threshold"),
                    "add_threshold": (view.get("config") or {}).get("add_threshold"),
                    "sell_threshold": (view.get("config") or {}).get("sell_threshold"),
                    "layer_weights": (view.get("config") or {}).get("layer_weights"),
                    "market_scale": (view.get("config") or {}).get("market_scale"),
                    "weights": (view.get("config") or {}).get("weights"),
                },
                "hint": "已写入我的策略。请到基础面板「今日关注」点击刷新候选池使新策略生效。",
            },
            ensure_ascii=False,
            default=str,
        )

    @tool
    def analyze_portfolio_positions() -> str:
        """对用户全部真实持仓做规则诊断：逐只给出卖出/持有/加仓建议、评分、浮盈亏与理由。
        分析持仓时优先调用本工具，再结合新闻工具补充叙事。
        """
        _bind()
        try:
            result = get_portfolio_advice(user_id=user_id)
        except Exception as exc:
            return json.dumps(
                {"error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False
            )
        items_out = []
        for it in result.get("items") or []:
            layers = it.get("layer_scores") or {}
            day_chg = it.get("day_chg_pct")
            pnl_pct = it.get("pnl_pct")
            items_out.append(
                {
                    "symbol": it.get("symbol"),
                    "name": it.get("name"),
                    "score": it.get("score"),
                    "action": it.get("action"),
                    "action_label": it.get("action_label"),
                    "rationale": (it.get("rationale") or "")[:500],
                    "layer_scores": {
                        k: layers.get(k)
                        for k in (
                            "tech_score",
                            "flow_score",
                            "sector_score",
                            "value_score",
                            "market_score",
                        )
                        if layers.get(k) is not None
                    }
                    or None,
                    "close": it.get("close"),
                    "day_chg_pct": day_chg,
                    "day_chg": _fmt_ratio_pct(day_chg),
                    "pnl": it.get("pnl"),
                    "pnl_pct": pnl_pct,
                    "pnl_chg": _fmt_ratio_pct(pnl_pct),
                    "position": it.get("position"),
                    "error": it.get("error"),
                }
            )
        return json.dumps(
            {
                "count": result.get("count"),
                "summary": result.get("summary"),
                "items": items_out,
                "disclaimer": "规则评分建议，仅供研究参考",
            },
            ensure_ascii=False,
            default=str,
        )

    @tool
    def upsert_real_position(
        symbol: str,
        qty: float,
        cost: float,
        name: str = "",
        note: str = "",
        confirm: bool = False,
    ) -> str:
        """新增或更新真实持仓一只。qty=数量，cost=成本价。必须 confirm=true 才落库。"""
        _bind()
        preview = {
            "symbol": symbol,
            "qty": qty,
            "cost": cost,
            "name": name or None,
            "note": note or None,
        }
        if not confirm:
            return _need_confirm("upsert_real_position", preview)
        try:
            port = upsert_position(
                user_id,
                symbol=symbol,
                qty=float(qty),
                cost=float(cost),
                name=name or None,
                note=note or None,
            )
        except Exception as exc:
            return json.dumps(
                {"applied": False, "error": f"{type(exc).__name__}: {exc}"},
                ensure_ascii=False,
            )
        return json.dumps(
            {
                "applied": True,
                "count": len(port.get("positions") or []),
                "positions": port.get("positions"),
            },
            ensure_ascii=False,
            default=str,
        )

    @tool
    def remove_real_position(symbol: str, confirm: bool = False) -> str:
        """从真实持仓中删除一只。必须 confirm=true 才落库。"""
        _bind()
        if not confirm:
            return _need_confirm("remove_real_position", {"symbol": symbol})
        try:
            port = remove_position(user_id, symbol)
        except Exception as exc:
            return json.dumps(
                {"applied": False, "error": f"{type(exc).__name__}: {exc}"},
                ensure_ascii=False,
            )
        return json.dumps(
            {
                "applied": True,
                "removed": symbol,
                "count": len(port.get("positions") or []),
                "positions": port.get("positions"),
            },
            ensure_ascii=False,
            default=str,
        )

    @tool
    def replace_real_portfolio(positions_json: str, confirm: bool = False) -> str:
        """用完整列表替换真实持仓。positions_json 为 [{symbol,qty,cost,name?,note?},...]。
        必须 confirm=true 才落库。空数组表示清空。
        """
        _bind()
        try:
            raw = json.loads(positions_json)
        except json.JSONDecodeError as exc:
            return json.dumps(
                {"applied": False, "error": f"JSON 解析失败: {exc}"},
                ensure_ascii=False,
            )
        if not isinstance(raw, list):
            return json.dumps(
                {"applied": False, "error": "positions_json 必须是数组"},
                ensure_ascii=False,
            )
        if not confirm:
            return _need_confirm(
                "replace_real_portfolio",
                {"count": len(raw), "positions": raw[:30]},
            )
        try:
            port = save_portfolio(PortfolioPayload(positions=raw), user_id)
        except Exception as exc:
            return json.dumps(
                {"applied": False, "error": f"{type(exc).__name__}: {exc}"},
                ensure_ascii=False,
            )
        return json.dumps(
            {
                "applied": True,
                "count": len(port.get("positions") or []),
                "positions": port.get("positions"),
            },
            ensure_ascii=False,
            default=str,
        )

    @tool
    def get_paper_account_detail() -> str:
        """获取模拟盘账户详情：现金、持仓列表（成本/现价/数量）。"""
        _bind()
        acc = get_account(user_id, mark_to_market=False)
        positions = [
            {
                "symbol": p.get("symbol"),
                "name": p.get("name"),
                "qty": p.get("qty"),
                "cost": p.get("cost"),
                "last": p.get("last"),
            }
            for p in (acc.get("positions") or [])
        ]
        return json.dumps(
            {
                "cash": acc.get("cash"),
                "equity": acc.get("equity"),
                "market_value": acc.get("market_value"),
                "initial_cash": acc.get("initial_cash"),
                "positions": positions,
            },
            ensure_ascii=False,
            default=str,
        )

    @tool
    def list_paper_trades(limit: int = 30) -> str:
        """列出模拟盘近期成交（买卖记录）。"""
        _bind()
        n = max(1, min(int(limit), 100))
        rows = list_trades(user_id, limit=n)
        if isinstance(rows, dict):
            trades = rows.get("trades") or []
        else:
            trades = rows
        slim = [
            {
                "symbol": t.get("symbol"),
                "name": t.get("name"),
                "side": t.get("side"),
                "qty": t.get("qty"),
                "price": t.get("price"),
                "amount": t.get("amount"),
                "source": t.get("source"),
                "created_at": t.get("created_at"),
            }
            for t in trades[:n]
        ]
        return json.dumps(
            {"count": len(slim), "trades": slim},
            ensure_ascii=False,
            default=str,
        )

    @tool
    def paper_place_order(
        side: str,
        symbol: str,
        qty: float,
        price: float = 0,
        confirm: bool = False,
    ) -> str:
        """模拟盘下单。side=buy|sell；price=0 表示按最新价。必须 confirm=true 才成交。"""
        _bind()
        side_v = (side or "").strip().lower()
        if side_v not in ("buy", "sell"):
            return json.dumps(
                {"applied": False, "error": "side 必须是 buy 或 sell"},
                ensure_ascii=False,
            )
        preview = {
            "side": side_v,
            "symbol": symbol,
            "qty": qty,
            "price": price if price and price > 0 else "市价/最新价",
        }
        if not confirm:
            return _need_confirm("paper_place_order", preview)
        try:
            body = PaperOrderBody(
                symbol=symbol,
                side=side_v,  # type: ignore[arg-type]
                qty=float(qty),
                price=float(price) if price and float(price) > 0 else None,
            )
            result = place_order(user_id, body, source="agent")
        except Exception as exc:
            return json.dumps(
                {"applied": False, "error": f"{type(exc).__name__}: {exc}"},
                ensure_ascii=False,
            )
        trade = result.get("trade") or {}
        acc = result.get("account") or {}
        return json.dumps(
            {
                "applied": True,
                "trade": {
                    "symbol": trade.get("symbol"),
                    "side": trade.get("side"),
                    "qty": trade.get("qty"),
                    "price": trade.get("price"),
                    "amount": trade.get("amount"),
                },
                "cash": acc.get("cash"),
                "positions_count": len(acc.get("positions") or []),
            },
            ensure_ascii=False,
            default=str,
        )

    @tool
    def paper_sell_all(confirm: bool = False) -> str:
        """模拟盘一键全部卖出。必须 confirm=true 才执行。"""
        _bind()
        if not confirm:
            acc = get_account(user_id, mark_to_market=False)
            return _need_confirm(
                "paper_sell_all",
                {
                    "positions_count": len(acc.get("positions") or []),
                    "symbols": [p.get("symbol") for p in (acc.get("positions") or [])],
                },
            )
        try:
            result = sell_all_positions(user_id)
        except Exception as exc:
            return json.dumps(
                {"applied": False, "error": f"{type(exc).__name__}: {exc}"},
                ensure_ascii=False,
            )
        return json.dumps(
            {
                "applied": True,
                "trades_count": len(result.get("trades") or []),
                "errors": result.get("errors") or [],
                "cash": (result.get("account") or {}).get("cash"),
            },
            ensure_ascii=False,
            default=str,
        )

    @tool
    def paper_reset_account(cash: float = 100000, confirm: bool = False) -> str:
        """重置模拟盘：清空持仓并设置现金。必须 confirm=true 才执行。"""
        _bind()
        if not confirm:
            return _need_confirm("paper_reset_account", {"cash": cash})
        try:
            acc = reset_account(user_id, float(cash))
        except Exception as exc:
            return json.dumps(
                {"applied": False, "error": f"{type(exc).__name__}: {exc}"},
                ensure_ascii=False,
            )
        return json.dumps(
            {
                "applied": True,
                "cash": acc.get("cash"),
                "positions_count": len(acc.get("positions") or []),
            },
            ensure_ascii=False,
            default=str,
        )

    @tool
    def paper_delete_position(symbol: str, confirm: bool = False) -> str:
        """作废模拟盘某标的持仓（当作未买过，回补现金）。必须 confirm=true。"""
        _bind()
        if not confirm:
            return _need_confirm("paper_delete_position", {"symbol": symbol})
        try:
            acc = delete_position(user_id, symbol)
        except Exception as exc:
            return json.dumps(
                {"applied": False, "error": f"{type(exc).__name__}: {exc}"},
                ensure_ascii=False,
            )
        return json.dumps(
            {
                "applied": True,
                "deleted": symbol,
                "cash": acc.get("cash"),
                "positions_count": len(acc.get("positions") or []),
            },
            ensure_ascii=False,
            default=str,
        )

    @tool
    def list_recommendation_dates(limit: int = 20) -> str:
        """列出用户推荐归档日期（最近若干交易日）。"""
        _bind()
        dates = list_snapshot_dates(limit=max(1, min(int(limit), 60)), user_id=user_id)
        return json.dumps(
            {"count": len(dates), "dates": dates},
            ensure_ascii=False,
        )

    @tool
    def get_recommendation_archive(trade_date: str = "", board: str = "all") -> str:
        """按交易日读取推荐归档。trade_date 空则取当前有效日；board=etf/hs/star/all。"""
        _bind()
        td = (trade_date or "").strip()[:10] or effective_rec_date()
        if not has_snapshot(td, user_id=user_id):
            return json.dumps(
                {"trade_date": td, "message": "该日无归档"},
                ensure_ascii=False,
            )
        board_key = None if board in ("", "all") else board
        recs = snapshot_as_recommendations(td, board=board_key, user_id=user_id)
        boards = (recs or {}).get("boards") or {}
        summary = {
            "trade_date": td,
            "buy_threshold": (recs or {}).get("buy_threshold"),
            "boards": {
                bid: {
                    "count": block.get("count"),
                    "items": _slim_rec_items(block.get("items") or [], limit=15),
                }
                for bid, block in boards.items()
            },
        }
        return json.dumps(summary, ensure_ascii=False, default=str)

    @tool
    def get_leaderboard_brief(
        list_id: str = "gainers", board: str = "hs", top: int = 10
    ) -> str:
        """读取龙虎榜缓存摘要。list_id=gainers|losers|inflow|outflow；board=etf|hs|star。"""
        _bind()
        doc = load_leaderboard()
        if not doc or not doc.get("boards"):
            return json.dumps(
                {"message": "暂无龙虎榜缓存，请先在基础面板刷新龙虎榜"},
                ensure_ascii=False,
            )
        lid = list_id if list_id in LIST_LABELS else "gainers"
        bid = board if board in ("etf", "hs", "star") else "hs"
        items = ((doc.get("boards") or {}).get(lid) or {}).get(bid) or []
        n = max(1, min(int(top), 25))
        slim = [
            {
                "symbol": it.get("symbol"),
                "name": it.get("name"),
                "price": it.get("price"),
                "pct_chg": it.get("pct_chg"),
                "main_net_inflow": it.get("main_net_inflow"),
            }
            for it in items[:n]
        ]
        return json.dumps(
            {
                "trade_date": doc.get("trade_date"),
                "list_id": lid,
                "list_label": LIST_LABELS.get(lid),
                "board": bid,
                "items": slim,
            },
            ensure_ascii=False,
            default=str,
        )

    @tool
    def get_user_data_overview() -> str:
        """一览用户可访问数据：持仓数、模拟盘、策略版本、归档日数、LLM 是否已配置（不含密钥）。"""
        _bind()
        port = load_portfolio(user_id)
        acc = get_account(user_id, mark_to_market=False)
        view = strategy_public_view(get_user_strategy(user_id))
        dates = list_snapshot_dates(limit=5, user_id=user_id)
        llm = public_llm_settings(user_id)
        return json.dumps(
            {
                "portfolio_count": len(port.get("positions") or []),
                "paper": {
                    "cash": acc.get("cash"),
                    "equity": acc.get("equity"),
                    "positions_count": len(acc.get("positions") or []),
                },
                "strategy": {
                    "version": view.get("version"),
                    "source": view.get("source"),
                },
                "recommendation_dates_sample": dates,
                "llm": {
                    "configured": llm.get("configured"),
                    "model": llm.get("model"),
                    "key_hint": llm.get("key_hint"),
                },
                "hint": "可按需调用 get_portfolio_summary / get_paper_account_detail / "
                "list_paper_trades / get_user_strategy_config / list_recommendation_dates 等深入查看。",
            },
            ensure_ascii=False,
            default=str,
        )

    @tool
    def list_committee_runs(limit: int = 10) -> str:
        """只读列出当前用户最近的委员会会议；不得据此直接下单。"""
        _bind()
        from ..committee.repository import CommitteeRepository, encode_api

        rows = CommitteeRepository.from_default_database().list_runs(
            user_id,
            limit=max(1, min(int(limit), 30)),
        )
        return json.dumps(
            {
                "runs": [
                    {
                        "run_id": item.run_id,
                        "status": item.status.value,
                        "strategy_version": item.strategy_version,
                        "created_at": encode_api(item.created_at),
                    }
                    for item in rows
                ],
                "read_only": True,
                "approval_required": True,
            },
            ensure_ascii=False,
        )

    @tool
    def get_committee_final_decision(run_id: str) -> str:
        """只读查询当前用户某次委员会最终决定；执行必须走 approve API。"""
        _bind()
        from ..committee.repository import CommitteeRepository

        repository = CommitteeRepository.from_default_database()
        artifact = repository.latest_artifact(
            user_id,
            run_id,
            "final_decision",
        )
        return json.dumps(
            {
                "run_id": run_id,
                "decision": (
                    None if artifact is None else artifact.get("payload")
                ),
                "read_only": True,
                "approval_required": True,
            },
            ensure_ascii=False,
            default=str,
        )

    @tool
    def list_unstructured_data_sources() -> str:
        """列出可用的 AKShare 非结构化/资讯/宏观类数据工具及覆盖范围说明。"""
        return json.dumps(ustr.list_unstructured_capabilities(), ensure_ascii=False)

    @tool
    def fetch_stock_news(symbol: str, limit: int = 8) -> str:
        """拉取个股新闻（AKShare 东方财富）。用于解读舆情与事件。"""
        return json.dumps(
            ustr.fetch_stock_news(symbol, limit=max(1, min(limit, 20))),
            ensure_ascii=False,
            default=str,
        )

    @tool
    def fetch_stock_notices(symbol: str, limit: int = 6) -> str:
        """拉取个股公告列表（AKShare）。"""
        return json.dumps(
            ustr.fetch_stock_notices(symbol, limit=max(1, min(limit, 15))),
            ensure_ascii=False,
            default=str,
        )

    @tool
    def fetch_research_reports(symbol: str, limit: int = 5) -> str:
        """拉取个股研报列表（AKShare 东方财富）。"""
        return json.dumps(
            ustr.fetch_research_reports(symbol, limit=max(1, min(limit, 12))),
            ensure_ascii=False,
            default=str,
        )

    @tool
    def fetch_market_cctv_news(date: str = "", limit: int = 10) -> str:
        """拉取新闻联播（宏观/政策/公开政治报道参考）。date 可选 YYYYMMDD。"""
        return json.dumps(
            ustr.fetch_market_cctv_news(
                date=date or None, limit=max(1, min(limit, 20))
            ),
            ensure_ascii=False,
            default=str,
        )

    @tool
    def fetch_index_news_sentiment(limit: int = 12) -> str:
        """拉取指数新闻情绪数据（若 AKShare 接口可用）。"""
        return json.dumps(
            ustr.fetch_index_news_sentiment(limit=max(1, min(limit, 30))),
            ensure_ascii=False,
            default=str,
        )

    @tool
    def fetch_macro_china_snapshot(limit: int = 5) -> str:
        """中国宏观财经快照：CPI、LPR、货币供应、央行利率等（含货币政策相关）。"""
        return json.dumps(
            ustr.fetch_macro_china_snapshot(limit=max(1, min(limit, 12))),
            ensure_ascii=False,
            default=str,
        )

    @tool
    def fetch_economic_calendar(date: str = "", limit: int = 15) -> str:
        """经济日历（财经数据公布）。date 可选 YYYYMMDD，空则最近可用日。"""
        return json.dumps(
            ustr.fetch_economic_calendar(
                date=date or None, limit=max(1, min(limit, 40))
            ),
            ensure_ascii=False,
            default=str,
        )

    @tool
    def fetch_market_indices() -> str:
        """获取 A 股主要指数实时行情（上证/深成/创业板/科创50/沪深300 等）。
        用户问指数点位、涨跌、大盘概况时必须调用；勿编造点位。"""
        from app.market import featured_indices_snapshot, get_market

        try:
            snap = featured_indices_snapshot(get_market())
        except Exception as exc:
            snap = {
                "error": f"{type(exc).__name__}: {exc}",
                "indices": [],
            }
        return json.dumps(snap, ensure_ascii=False, default=str)

    @tool
    def fetch_index_extremes(query: str = "科创50") -> str:
        """查询主要指数历史极值：盘中历史最高（日线 high 最大）、历史最高收盘、最新收盘与回撤。
        问「历史最高点/前高/距高点差多少」时必须调用；query 可为名称或代码，如 科创50、000688。"""
        from app.market import fetch_index_extremes as _fetch

        try:
            data = _fetch(query)
        except Exception as exc:
            data = {
                "query": query,
                "error": f"{type(exc).__name__}: {exc}",
            }
        return json.dumps(data, ensure_ascii=False, default=str)

    @tool
    def fetch_symbol_daily_ma(symbol: str, recent: int = 30) -> str:
        """获取个股日 K 与 MA5/MA10/MA20。返回最新收盘、三条均线及相对位置，并附最近若干根日 K（含当日均线）。
        问某票日线走势、站上/跌破均线、均线多头空头时必须调用；勿编造均线数值。"""
        from app.kline import fetch_symbol_daily_ma as _fetch

        try:
            data = _fetch(symbol, recent=max(5, min(int(recent), 60)))
        except Exception as exc:
            data = {
                "symbol": symbol,
                "error": f"{type(exc).__name__}: {exc}",
            }
        return json.dumps(data, ensure_ascii=False, default=str)

    @tool
    def load_knowledge(knowledge_id: str) -> str:
        """按 id 加载用户可选/知识库正文。目录在系统提示「用户可选知识目录」中。
        仅能加载当前用户且已启用的条目。"""
        from ..knowledge import list_raw

        _bind()
        kid = (knowledge_id or "").strip()
        raw = next((x for x in list_raw(user_id) if x.get("id") == kid), None)
        if not raw:
            return json.dumps(
                {"error": "知识条目不存在", "id": kid}, ensure_ascii=False
            )
        if not raw.get("enabled"):
            return json.dumps(
                {"error": "知识条目已禁用", "id": kid}, ensure_ascii=False
            )
        return json.dumps(
            {
                "id": raw.get("id"),
                "title": raw.get("title"),
                "mode": raw.get("mode"),
                "body": raw.get("body") or "",
            },
            ensure_ascii=False,
        )

    return [
        get_today_recommendations,
        get_portfolio_summary,
        upsert_real_position,
        remove_real_position,
        replace_real_portfolio,
        analyze_portfolio_positions,
        get_paper_pnl_brief,
        get_paper_account_detail,
        list_paper_trades,
        paper_place_order,
        paper_sell_all,
        paper_reset_account,
        paper_delete_position,
        get_symbol_advice,
        get_user_strategy_config,
        propose_strategy_patch,
        apply_strategy_patch,
        list_recommendation_dates,
        get_recommendation_archive,
        get_leaderboard_brief,
        get_user_data_overview,
        list_committee_runs,
        get_committee_final_decision,
        list_unstructured_data_sources,
        fetch_stock_news,
        fetch_stock_notices,
        fetch_research_reports,
        fetch_market_cctv_news,
        fetch_index_news_sentiment,
        fetch_macro_china_snapshot,
        fetch_economic_calendar,
        fetch_market_indices,
        fetch_index_extremes,
        fetch_symbol_daily_ma,
        load_knowledge,
        build_delegate_data_tool(user_id),
    ]
