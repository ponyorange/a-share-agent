"""Advisor orchestration: recommendations + single-symbol advice."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from ..kline import normalize_symbol
from .backtest import hit_rate_for_symbol
from .config_loader import load_config
from . import context
from .features import build_feature_result, fetch_daily_df, load_benchmark
from .portfolio import get_position, has_position, load_portfolio
from .scoring import (
    action_label,
    build_rationale,
    decide_action,
    score_features,
)
from .universe import name_for
from .market_context import (
    enrich_symbol_context,
    fetch_industry_strength_map,
    get_market_score,
)
from .regime import get_regime_for_gate
from .regime.gate import apply_regime_gate
from .signal_graph.service import attach_graph_fields, signal_graph_config


def _maybe_attach_graph(
    row: dict[str, Any],
    *,
    for_recommendations: bool = False,
    for_advice: bool = False,
) -> dict[str, Any]:
    cfg = signal_graph_config()
    if not cfg.get("enabled"):
        return row
    if for_recommendations and not cfg.get("attach_to_recommendations"):
        return row
    if for_advice and not cfg.get("attach_to_advice"):
        return row
    try:
        return attach_graph_fields(row, persist=True)
    except Exception as exc:
        row["graph_signal"] = {"error": str(exc)}
        return row


def _stamp_recommendation_picks(
    picks: list[dict[str, Any]],
    hit_map: dict[str, float | None],
    *,
    attach_graph: bool = True,
) -> list[dict[str, Any]]:
    """Attach hit_rate and optionally graph_signal onto ranked recommendation rows."""
    for p in picks:
        p["hit_rate"] = hit_map.get(p["symbol"])
        if attach_graph:
            _maybe_attach_graph(p, for_recommendations=True)
    return picks


def _iter_graph_stamp_events(picks: list[dict[str, Any]]):
    """Yield SSE progress while attaching graph_signal onto ranked picks."""
    total = len(picks)
    if not total:
        return
    yield {
        "event": "progress",
        "data": {
            "phase": "graph",
            "done": 0,
            "total": total,
            "message": f"挂图 0/{total}",
        },
    }
    for i, p in enumerate(picks, 1):
        _maybe_attach_graph(p, for_recommendations=True)
        yield {
            "event": "progress",
            "data": {
                "phase": "graph",
                "done": i,
                "total": total,
                "symbol": p.get("symbol"),
                "name": p.get("name"),
                "message": f"挂图 {i}/{total}",
            },
        }


def _analyze_symbol(
    symbol: str,
    name_hint: str | None,
    bench_df,
    as_of: str | None,
    force_has_position: bool | None,
    *,
    industry_map: dict[str, Any] | None = None,
    market_ctx: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    try:
        symbol = normalize_symbol(symbol)
        fetched_name, df = fetch_daily_df(symbol)
    except Exception as exc:
        return {
            "symbol": symbol,
            "error": str(exc),
        }

    name = name_hint or fetched_name or name_for(symbol)
    feat = build_feature_result(symbol, name, df, bench_df, as_of=as_of)
    if feat is None:
        return {
            "symbol": symbol,
            "name": name,
            "error": "历史日线不足，无法评分",
        }

    ctx = enrich_symbol_context(
        symbol, industry_map=industry_map, market=market_ctx
    )
    score, contribs, layer_detail = score_features(
        feat,
        context_scores={
            "flow_score": ctx["flow_score"],
            "sector_score": ctx["sector_score"],
            "value_score": ctx["value_score"],
            "market_score": ctx["market_score"],
        },
    )
    held = (
        force_has_position
        if force_has_position is not None
        else has_position(symbol)
    )
    action = decide_action(score, held)
    position = get_position(symbol) if held else None

    cfg = load_config()
    return {
        "symbol": symbol,
        "name": name,
        "as_of": feat.as_of,
        "close": feat.close,
        "prev_close": feat.prev_close,
        "day_chg_pct": feat.day_chg_pct,
        "score": score,
        "action": action,
        "action_label": action_label(action, held),
        "has_position": held,
        "position": position,
        "factors": contribs,
        "layer_scores": layer_detail,
        "industry": (ctx.get("sector") or {}).get("industry"),
        "raw_factors": {
            k: (None if isinstance(v, float) and v != v else v)
            for k, v in feat.factors.items()
        },
        "hit_rate": None,  # filled by caller when needed
        "rationale": build_rationale(
            action, score, contribs, held, layer_detail=layer_detail
        ),
        "disclaimer": cfg.get("disclaimer"),
    }


def get_advice(symbol: str, as_of: str | None = None) -> dict[str, Any]:
    bench = load_benchmark(as_of)
    result = _analyze_symbol(symbol, None, bench, as_of, None)
    if result is None:
        raise RuntimeError("分析失败")
    if result.get("error") and "score" not in result:
        raise ValueError(result["error"])
    try:
        result["hit_rate"] = hit_rate_for_symbol(result["symbol"], bench)
    except Exception:
        result["hit_rate"] = None
    return _maybe_attach_graph(result, for_advice=True)


def _enrich_pnl(row: dict[str, Any]) -> dict[str, Any]:
    """Attach floating PnL from position cost vs last close."""
    pos = row.get("position") or {}
    close = row.get("close")
    cost = pos.get("cost")
    qty = pos.get("qty")
    if (
        close is not None
        and cost is not None
        and qty is not None
        and float(cost) > 0
        and float(qty) > 0
    ):
        c = float(close)
        k = float(cost)
        q = float(qty)
        pnl_pct = c / k - 1.0
        pnl = (c - k) * q
        row["pnl_pct"] = round(pnl_pct, 6)
        row["pnl"] = round(pnl, 2)
    else:
        row["pnl_pct"] = None
        row["pnl"] = None
    return row


def get_portfolio_advice(
    as_of: str | None = None,
    max_workers: int = 6,
    user_id: str | None = None,
) -> dict[str, Any]:
    """Diagnose every holding with sell/hold/add actions."""
    cfg = load_config()
    portfolio = load_portfolio(user_id)
    positions = portfolio.get("positions") or []
    if not positions:
        return {
            "as_of": as_of,
            "count": 0,
            "items": [],
            "summary": {"add": 0, "hold": 0, "sell": 0, "error": 0},
            "disclaimer": cfg.get("disclaimer"),
        }

    if user_id:
        context.set_user_id(user_id)

    bench = load_benchmark(as_of)
    market_ctx = get_market_score(as_of)
    industry_map = fetch_industry_strength_map(
        str(as_of or "")[:10] or None
    )
    rows: list[dict[str, Any]] = []

    def _job(pos: dict[str, Any]) -> dict[str, Any]:
        # ThreadPool 不继承 ContextVar，分析时显式带上持仓
        if user_id:
            context.set_user_id(user_id)
        row = _analyze_symbol(
            str(pos["symbol"]),
            pos.get("name"),
            bench,
            as_of,
            force_has_position=True,
            industry_map=industry_map,
            market_ctx=market_ctx,
        )
        if row is None:
            return {"symbol": pos.get("symbol"), "error": "分析失败"}
        if "score" in row:
            # ensure position from file (cost/qty) is attached
            row["position"] = pos
            row["has_position"] = True
            _enrich_pnl(row)
            _maybe_attach_graph(row, for_advice=True)
        return row

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [pool.submit(_job, p) for p in positions]
        for fut in as_completed(futures):
            try:
                rows.append(fut.result())
            except Exception as exc:
                rows.append({"symbol": "?", "error": str(exc)})

    ok = [r for r in rows if "score" in r]
    # Priority: sell first, then hold, then add — within group by score asc for sell, desc for add
    action_rank = {"sell": 0, "hold": 1, "add": 2}

    def _sort_key(r: dict[str, Any]) -> tuple:
        act = str(r.get("action") or "hold")
        score = float(r.get("score") or 0)
        if act == "sell":
            return (action_rank.get(act, 9), score)  # weaker first among sells
        return (action_rank.get(act, 9), -score)

    ok.sort(key=_sort_key)
    errors = [r for r in rows if r.get("error") and "score" not in r]

    summary = {
        "add": sum(1 for r in ok if r.get("action") == "add"),
        "hold": sum(1 for r in ok if r.get("action") == "hold"),
        "sell": sum(1 for r in ok if r.get("action") == "sell"),
        "error": len(errors),
    }

    return {
        "as_of": as_of or (ok[0]["as_of"] if ok else None),
        "count": len(ok),
        "items": ok,
        "errors": errors,
        "summary": summary,
        "disclaimer": cfg.get("disclaimer"),
    }


def _coarse_fallback_row(
    item: dict[str, Any],
    board: str,
    board_label: str,
) -> dict[str, Any]:
    """When daily kline fails, still surface a coarse-only recommendation."""
    cfg = load_config()
    score = float(item.get("coarse_score") or 0.5)
    held = has_position(str(item["symbol"]))
    action = decide_action(score, held)
    # spot pct_chg 一般为百分点，如 2.5 → 2.5%
    day_chg = None
    if item.get("pct_chg") is not None:
        try:
            day_chg = round(float(item["pct_chg"]) / 100.0, 6)
        except (TypeError, ValueError):
            day_chg = None
    return {
        "symbol": item["symbol"],
        "name": item.get("name") or item["symbol"],
        "as_of": None,
        "close": item.get("price"),
        "day_chg_pct": day_chg,
        "score": score,
        "action": action,
        "action_label": action_label(action, held),
        "has_position": held,
        "position": get_position(str(item["symbol"])) if held else None,
        "factors": [],
        "coarse_score": score,
        "score_source": "coarse_only",
        "hit_rate": None,
        "board": board,
        "board_label": board_label,
        "rationale": (
            f"日线精算暂不可用，暂用行情粗分 {score:.2f}"
            f"（{action_label(action, held)}）。建议稍后重试精算。"
        ),
        "disclaimer": cfg.get("disclaimer"),
    }


def get_recommendations(
    top: int = 15,
    as_of: str | None = None,
    board: str | None = None,
    max_workers: int = 3,
    force_universe: bool = False,
    user_id: str | None = None,
    regime_override: bool = False,
    apply_regime: bool = True,
) -> dict[str, Any]:
    """大池 spot 粗筛 → 仅对 Top 精算（日线因子）。"""
    from .screen import select_for_precise
    from .universe import (
        BOARD_LABELS,
        BoardId,
        build_universe,
        list_board_candidates,
        precise_limits,
    )

    if user_id:
        context.set_user_id(user_id)

    cfg = load_config()
    top = max(1, min(top, 50))
    buy_th = float(cfg.get("buy_threshold", 0.55))
    limits_precise = precise_limits()

    board_ids: list[BoardId]
    if board in ("etf", "hs", "star"):
        board_ids = [board]  # type: ignore[list-item]
    else:
        board_ids = ["etf", "hs", "star"]

    uni = build_universe(force=force_universe)
    bench = load_benchmark(as_of)
    # 预取市场/行业上下文（全板共用，失败则中性）
    market_ctx = get_market_score(as_of)
    industry_map = fetch_industry_strength_map(
        str(as_of or "")[:10] or None
    )

    hit_map: dict[str, float | None] = {}
    strategy_hit = None
    try:
        from .backtest import _cache as _bt_cache

        cached = (_bt_cache.get("data") or {}).get("per_symbol") or []
        hit_map = {r["symbol"]: r.get("hit_rate") for r in cached if r.get("symbol")}
        if _bt_cache.get("data"):
            strategy_hit = _bt_cache["data"].get("hit_rate")
    except Exception:
        pass

    boards_out: dict[str, Any] = {}
    all_errors: list[dict[str, Any]] = []

    for bid in board_ids:
        pool = list_board_candidates(bid, force=False)
        shortlist = select_for_precise(pool, bid, limits_precise.get(bid, 25))
        rows: list[dict[str, Any]] = []
        coarse_by_sym = {c["symbol"]: c for c in shortlist}

        def _job(item: dict[str, Any]) -> dict[str, Any] | None:
            # ThreadPool 不会自动继承 ContextVar，需在子线程重绑
            if user_id:
                context.bind_user(user_id)
            return _analyze_symbol(
                item["symbol"],
                item.get("name"),
                bench,
                as_of,
                force_has_position=None,
                industry_map=industry_map,
                market_ctx=market_ctx,
            )

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(_job, u): u for u in shortlist}
            for fut in as_completed(futures):
                u = futures[fut]
                try:
                    row = fut.result()
                except Exception as exc:
                    all_errors.append(
                        {"symbol": u["symbol"], "board": bid, "error": str(exc)}
                    )
                    rows.append(
                        _coarse_fallback_row(u, bid, BOARD_LABELS[bid])
                    )
                    continue
                if row and "score" in row:
                    row["board"] = bid
                    row["board_label"] = BOARD_LABELS[bid]
                    row["coarse_score"] = (coarse_by_sym.get(u["symbol"]) or {}).get(
                        "coarse_score"
                    )
                    row["score_source"] = "precise"
                    rows.append(row)
                elif row and row.get("error"):
                    all_errors.append(
                        {"symbol": u.get("symbol"), "board": bid, "error": row["error"]}
                    )
                    rows.append(
                        _coarse_fallback_row(u, bid, BOARD_LABELS[bid])
                    )

        ranked = sorted(rows, key=lambda r: r["score"], reverse=True)
        # Prefer precise rows that clear buy threshold; else top by score (incl. coarse)
        precise_ok = [
            r
            for r in ranked
            if r.get("score_source") == "precise" and r["score"] >= buy_th
        ]
        picks = precise_ok[:top]
        if len(picks) < min(5, top):
            picks = ranked[:top]
        _stamp_recommendation_picks(picks, hit_map)

        boards_out[bid] = {
            "id": bid,
            "label": BOARD_LABELS[bid],
            "pool_size": len(pool),
            "precise_size": len(shortlist),
            "scanned": len(shortlist),
            "count": len(picks),
            "items": picks,
            "precise_ok": sum(1 for r in rows if r.get("score_source") == "precise"),
            "coarse_fallback": sum(
                1 for r in rows if r.get("score_source") == "coarse_only"
            ),
        }

    if len(board_ids) == 1:
        flat = boards_out[board_ids[0]]["items"]
        scanned = boards_out[board_ids[0]]["scanned"]
        pool_total = boards_out[board_ids[0]]["pool_size"]
    else:
        flat = []
        for bid in board_ids:
            flat.extend(boards_out[bid]["items"])
        scanned = sum(boards_out[b]["scanned"] for b in board_ids)
        pool_total = sum(boards_out[b]["pool_size"] for b in board_ids)

    strategy_meta: dict[str, Any] = {}
    if user_id:
        try:
            from .user_strategy import get_user_strategy

            st = get_user_strategy(user_id)
            strategy_meta = {
                "strategy_version": st.get("version"),
                "strategy_source": st.get("source"),
            }
        except Exception:
            pass

    result = {
        "as_of": as_of
        or next(
            (i["as_of"] for b in board_ids for i in boards_out[b]["items"] if i.get("as_of")),
            None,
        ),
        "count": len(flat),
        "buy_threshold": buy_th,
        "strategy_hit_rate": strategy_hit,
        "items": flat,
        "scanned": scanned,
        "pool_total": pool_total,
        "mode": "coarse_then_precise",
        "board": board if board in ("etf", "hs", "star") else "all",
        "boards": boards_out,
        "universe_source": uni.get("source"),
        "errors": all_errors,
        "disclaimer": cfg.get("disclaimer"),
        "market_context": {
            "score": market_ctx.get("score"),
            "trade_date": market_ctx.get("trade_date"),
            "northbound_ok": (market_ctx.get("northbound") or {}).get("ok"),
            "trend_ok": (market_ctx.get("trend") or {}).get("ok"),
            "regime_ok": (market_ctx.get("regime") or {}).get("ok"),
        },
        **strategy_meta,
    }
    if not apply_regime:
        return result
    return apply_regime_gate(result, get_regime_for_gate(allow_stale=True), override=regime_override)


def iter_recommendations_refresh_events(
    *,
    top: int = 15,
    as_of: str | None = None,
    board: str | None = None,
    max_workers: int = 3,
    user_id: str | None = None,
    persist: bool = True,
    trade_date: str | None = None,
):
    """SSE：强制重建候选池并精算。meta → progress* → done|error。

    progress.phase: universe | screen | precise | persist
    """
    from .screen import select_for_precise
    from .snapshots import save_snapshot
    from .universe import (
        BOARD_LABELS,
        BoardId,
        iter_build_universe_events,
        list_board_candidates,
        precise_limits,
    )

    if user_id:
        context.set_user_id(user_id)

    cfg = load_config()
    top = max(1, min(int(top), 50))
    buy_th = float(cfg.get("buy_threshold", 0.55))
    limits_precise = precise_limits()

    board_ids: list[BoardId]
    if board in ("etf", "hs", "star"):
        board_ids = [board]  # type: ignore[list-item]
    else:
        board_ids = ["etf", "hs", "star"]

    td = (trade_date or as_of or "")[:10] or None
    yield {
        "event": "meta",
        "data": {
            "trade_date": td,
            "top": top,
            "board": board if board in ("etf", "hs", "star") else "all",
            "phase": "universe",
        },
    }

    uni: dict[str, Any] | None = None
    try:
        for ev in iter_build_universe_events(force=True):
            if ev.get("event") == "progress":
                yield ev
            elif ev.get("event") == "done":
                uni = ev.get("data")
            elif ev.get("event") == "error":
                yield ev
                return
    except Exception as exc:
        yield {
            "event": "error",
            "data": {"detail": f"候选池重建失败: {type(exc).__name__}: {exc}"},
        }
        return

    if not uni:
        yield {"event": "error", "data": {"detail": "候选池为空"}}
        return

    yield {
        "event": "progress",
        "data": {
            "phase": "screen",
            "message": "粗筛精算名单…",
            "done": 0,
            "total": 1,
        },
    }

    try:
        bench = load_benchmark(as_of)
        market_ctx = get_market_score(as_of)
        industry_map = fetch_industry_strength_map(
            str(as_of or "")[:10] or None
        )

        hit_map: dict[str, float | None] = {}
        strategy_hit = None
        try:
            from .backtest import _cache as _bt_cache

            cached = (_bt_cache.get("data") or {}).get("per_symbol") or []
            hit_map = {
                r["symbol"]: r.get("hit_rate") for r in cached if r.get("symbol")
            }
            if _bt_cache.get("data"):
                strategy_hit = _bt_cache["data"].get("hit_rate")
        except Exception:
            pass

        jobs: list[tuple[BoardId, dict[str, Any]]] = []
        pools: dict[str, list[dict[str, Any]]] = {}
        shortlists: dict[str, list[dict[str, Any]]] = {}
        for bid in board_ids:
            pool = list_board_candidates(bid, force=False)
            shortlist = select_for_precise(pool, bid, limits_precise.get(bid, 25))
            pools[bid] = pool
            shortlists[bid] = shortlist
            for u in shortlist:
                jobs.append((bid, u))

        yield {
            "event": "progress",
            "data": {
                "phase": "screen",
                "message": f"粗筛完成，待精算 {len(jobs)} 只",
                "done": 1,
                "total": 1,
                "precise_total": len(jobs),
            },
        }

        boards_out: dict[str, Any] = {}
        all_errors: list[dict[str, Any]] = []
        rows_by_board: dict[str, list[dict[str, Any]]] = {b: [] for b in board_ids}
        coarse_by_board: dict[str, dict[str, dict[str, Any]]] = {
            b: {c["symbol"]: c for c in shortlists[b]} for b in board_ids
        }

        total = max(len(jobs), 1)
        done_n = 0

        def _job(item: dict[str, Any]) -> dict[str, Any] | None:
            if user_id:
                context.bind_user(user_id)
            return _analyze_symbol(
                item["symbol"],
                item.get("name"),
                bench,
                as_of,
                force_has_position=None,
                industry_map=industry_map,
                market_ctx=market_ctx,
            )

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(_job, u): (bid, u) for bid, u in jobs
            }
            for fut in as_completed(futures):
                bid, u = futures[fut]
                done_n += 1
                try:
                    row = fut.result()
                except Exception as exc:
                    all_errors.append(
                        {"symbol": u["symbol"], "board": bid, "error": str(exc)}
                    )
                    rows_by_board[bid].append(
                        _coarse_fallback_row(u, bid, BOARD_LABELS[bid])
                    )
                    yield {
                        "event": "progress",
                        "data": {
                            "phase": "precise",
                            "done": done_n,
                            "total": total,
                            "symbol": u.get("symbol"),
                            "name": u.get("name"),
                            "board": bid,
                            "message": f"精算 {done_n}/{total}",
                        },
                    }
                    continue
                if row and "score" in row:
                    row["board"] = bid
                    row["board_label"] = BOARD_LABELS[bid]
                    row["coarse_score"] = (
                        coarse_by_board.get(bid) or {}
                    ).get(u["symbol"], {}).get("coarse_score")
                    row["score_source"] = "precise"
                    rows_by_board[bid].append(row)
                elif row and row.get("error"):
                    all_errors.append(
                        {
                            "symbol": u.get("symbol"),
                            "board": bid,
                            "error": row["error"],
                        }
                    )
                    rows_by_board[bid].append(
                        _coarse_fallback_row(u, bid, BOARD_LABELS[bid])
                    )
                yield {
                    "event": "progress",
                    "data": {
                        "phase": "precise",
                        "done": done_n,
                        "total": total,
                        "symbol": u.get("symbol"),
                        "name": u.get("name"),
                        "board": bid,
                        "message": f"精算 {done_n}/{total}",
                    },
                }

        for bid in board_ids:
            rows = rows_by_board[bid]
            ranked = sorted(rows, key=lambda r: r["score"], reverse=True)
            precise_ok = [
                r
                for r in ranked
                if r.get("score_source") == "precise" and r["score"] >= buy_th
            ]
            picks = precise_ok[:top]
            if len(picks) < min(5, top):
                picks = ranked[:top]
            # 先打命中率并落库，挂图单独报进度，避免卡在精算 75/75
            _stamp_recommendation_picks(picks, hit_map, attach_graph=False)
            boards_out[bid] = {
                "id": bid,
                "label": BOARD_LABELS[bid],
                "pool_size": len(pools[bid]),
                "precise_size": len(shortlists[bid]),
                "scanned": len(shortlists[bid]),
                "count": len(picks),
                "items": picks,
                "precise_ok": sum(
                    1 for r in rows if r.get("score_source") == "precise"
                ),
                "coarse_fallback": sum(
                    1 for r in rows if r.get("score_source") == "coarse_only"
                ),
            }

        if len(board_ids) == 1:
            flat = boards_out[board_ids[0]]["items"]
            scanned = boards_out[board_ids[0]]["scanned"]
            pool_total = boards_out[board_ids[0]]["pool_size"]
        else:
            flat = []
            for bid in board_ids:
                flat.extend(boards_out[bid]["items"])
            scanned = sum(boards_out[b]["scanned"] for b in board_ids)
            pool_total = sum(boards_out[b]["pool_size"] for b in board_ids)

        strategy_meta: dict[str, Any] = {}
        if user_id:
            try:
                from .user_strategy import get_user_strategy

                st = get_user_strategy(user_id)
                strategy_meta = {
                    "strategy_version": st.get("version"),
                    "strategy_source": st.get("source"),
                }
            except Exception:
                pass

        result: dict[str, Any] = {
            "as_of": as_of
            or next(
                (
                    i["as_of"]
                    for b in board_ids
                    for i in boards_out[b]["items"]
                    if i.get("as_of")
                ),
                None,
            ),
            "count": len(flat),
            "buy_threshold": buy_th,
            "strategy_hit_rate": strategy_hit,
            "items": flat,
            "scanned": scanned,
            "pool_total": pool_total,
            "mode": "coarse_then_precise",
            "board": board if board in ("etf", "hs", "star") else "all",
            "boards": boards_out,
            "universe_source": uni.get("source"),
            "errors": all_errors,
            "disclaimer": cfg.get("disclaimer"),
            "market_context": {
                "score": market_ctx.get("score"),
                "trade_date": market_ctx.get("trade_date"),
                "northbound_ok": (market_ctx.get("northbound") or {}).get("ok"),
                "trend_ok": (market_ctx.get("trend") or {}).get("ok"),
                "regime_ok": (market_ctx.get("regime") or {}).get("ok"),
            },
            **strategy_meta,
        }
        if persist and user_id and td:
            yield {
                "event": "progress",
                "data": {
                    "phase": "persist",
                    "message": "写入归档…",
                    "done": 0,
                    "total": 1,
                },
            }
            snap = save_snapshot(result, trade_date=td, user_id=user_id)
            result["snapshot"] = snap
            result["trade_date"] = td
            yield {
                "event": "progress",
                "data": {
                    "phase": "persist",
                    "message": "归档完成",
                    "done": 1,
                    "total": 1,
                },
            }

        for ev in _iter_graph_stamp_events(flat):
            yield ev

        if persist and user_id and td:
            snap = save_snapshot(result, trade_date=td, user_id=user_id)
            result["snapshot"] = snap
            result["trade_date"] = td

        yield {"event": "done", "data": apply_regime_gate(result, get_regime_for_gate(allow_stale=True))}
    except Exception as exc:
        yield {
            "event": "error",
            "data": {"detail": f"推荐生成失败: {type(exc).__name__}: {exc}"},
        }

