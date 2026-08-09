"""Historical SignalGraph backfill over advisor universe pools."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Callable

import pandas as pd

from ...kline import normalize_symbol
from ..calendar_util import is_trading_day, last_trading_day
from ..features import build_feature_result, compute_factors, fetch_daily_df, load_benchmark
from ..universe import list_board_candidates
from .a_share_graph.feedback import FeedbackEngine
from .a_share_graph.market import normalize_ticker
from .a_share_graph.models import MarketState, SignalContext
from .a_share_graph.signals import SignalEngine
from .context_builder import (
    _industry_slug,
    _limit_flags,
    _price_on_or_before,
    infer_patterns,
    map_regime_to_graph,
)
from . import store as graph_store
from .service import (
    _WRITE_LOCK,
    date_for_tick,
    resolve_trade_tick,
    signal_graph_config,
)


ProgressFn = Callable[[dict[str, Any]], None]


@dataclass
class SymbolSeries:
    symbol: str
    ticker: str
    name: str
    df: pd.DataFrame
    industry: str


def _trading_days_ending(end: str, days: int) -> list[str]:
    """Return up to `days` trading days on/before end (ascending)."""
    out: list[str] = []
    d = date.fromisoformat(end[:10])
    guard = 0
    while len(out) < days and guard < days * 4 + 30:
        if is_trading_day(d):
            out.append(d.isoformat())
        d -= timedelta(days=1)
        guard += 1
    out.reverse()
    return out


def _regime_from_bench(bench: pd.DataFrame, trade_date: str) -> str:
    """Approximate market regime from recent benchmark return."""
    work = bench.copy()
    work["time"] = work["time"].astype(str).str.slice(0, 10)
    work = work[work["time"] <= trade_date[:10]]
    if len(work) < 21:
        return "sideways"
    last = float(work.iloc[-1]["close"])
    prev = float(work.iloc[-21]["close"])
    if prev <= 0:
        return "sideways"
    ret = last / prev - 1.0
    if ret >= 0.04:
        return "bull"
    if ret <= -0.04:
        return "bear"
    return "sideways"


def collect_universe_symbols(*, boards: tuple[str, ...] = ("etf", "hs", "star")) -> list[str]:
    symbols: list[str] = []
    seen: set[str] = set()
    for bid in boards:
        for row in list_board_candidates(bid, force=False):  # type: ignore[arg-type]
            sym = str(row.get("symbol") or "").strip()
            if not sym or sym in seen:
                continue
            seen.add(sym)
            symbols.append(sym)
    return symbols


def prefetch_series(
    symbols: list[str],
    *,
    max_workers: int = 8,
    on_progress: ProgressFn | None = None,
) -> tuple[dict[str, SymbolSeries], list[dict[str, str]]]:
    series: dict[str, SymbolSeries] = {}
    errors: list[dict[str, str]] = []
    total = len(symbols)
    done = 0

    def _one(sym: str) -> tuple[str, SymbolSeries | None, str | None]:
        try:
            code = normalize_symbol(sym)
            name, df = fetch_daily_df(code)
            if df is None or df.empty or len(df) < 30:
                return code, None, "日线不足"
            # Industry resolution is slow (per-symbol network); use unknown for backfill.
            industry = "unknown"
            return (
                code,
                SymbolSeries(
                    symbol=code,
                    ticker=normalize_ticker(code),
                    name=name or code,
                    df=df,
                    industry=industry,
                ),
                None,
            )
        except Exception as exc:
            return sym, None, str(exc)

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futs = {pool.submit(_one, s): s for s in symbols}
        for fut in as_completed(futs):
            code, item, err = fut.result()
            done += 1
            if item is not None:
                series[code] = item
            else:
                errors.append({"symbol": code, "error": err or "unknown"})
            if on_progress and (done % 10 == 0 or done == total):
                on_progress(
                    {
                        "phase": "prefetch",
                        "done": done,
                        "total": total,
                        "ok": len(series),
                        "errors": len(errors),
                    }
                )
    return series, errors


def _offline_inputs(
    item: SymbolSeries,
    *,
    trade_date: str,
    trade_tick: int,
    horizon_days: int,
    owner: str,
    bench: pd.DataFrame,
    market_regime: str,
) -> dict[str, Any] | None:
    feat = build_feature_result(
        item.symbol, item.name, item.df, bench, as_of=trade_date
    )
    if feat is None:
        return None
    entry = float(feat.close)
    bench_px = _price_on_or_before(bench, trade_date)
    if bench_px is None:
        return None
    # Prefer factors on the sliced window for patterns.
    work = item.df.copy()
    work["time"] = work["time"].astype(str).str.slice(0, 10)
    work = work[work["time"] <= trade_date[:10]]
    factors = compute_factors(work, bench) if len(work) >= 25 else feat.factors
    patterns = infer_patterns(factors)
    is_up, is_down = _limit_flags(
        close=feat.close,
        prev_close=feat.prev_close,
        day_chg_pct=feat.day_chg_pct,
    )
    context = SignalContext(
        ticker=item.ticker,
        trade_date=trade_date[:10],
        trade_tick=int(trade_tick),
        market_regime=market_regime,
        industry=_industry_slug(item.industry),
        patterns=patterns,
        horizon_days=int(horizon_days),
        owner=owner,
    )
    state = MarketState(
        ticker=item.ticker,
        is_suspended=False,
        is_limit_up=is_up,
        is_limit_down=is_down,
        is_st="ST" in str(item.name or "").upper(),
        trading_days_since_listing=int(feat.bars_used),
    )
    return {
        "context": context,
        "market_state": state,
        "entry_price": entry,
        "benchmark_entry_price": float(bench_px),
    }


def _exit_from_series(
    item: SymbolSeries,
    bench: pd.DataFrame,
    trade_date: str,
) -> tuple[float, float] | None:
    stock = _price_on_or_before(item.df, trade_date)
    bench_px = _price_on_or_before(bench, trade_date)
    if stock is None or bench_px is None:
        return None
    return float(stock), float(bench_px)


def run_universe_backfill(
    *,
    days: int = 60,
    end_date: str | None = None,
    boards: tuple[str, ...] = ("etf", "hs", "star"),
    reset: bool = True,
    max_workers: int = 8,
    persist_every: int = 5,
    on_progress: ProgressFn | None = None,
) -> dict[str, Any]:
    """Prefetch pool klines, replay generate+settle day-by-day, persist graph."""
    cfg = signal_graph_config()
    if not cfg["enabled"]:
        raise RuntimeError("signal_graph 未启用")
    owner = cfg["owner"]
    horizon = int(cfg["horizon_days"])
    end = (end_date or last_trading_day())[:10]
    trade_days = _trading_days_ending(end, max(1, int(days)))

    t0 = time.time()
    symbols = collect_universe_symbols(boards=boards)
    if on_progress:
        on_progress(
            {
                "phase": "universe",
                "symbols": len(symbols),
                "trade_days": len(trade_days),
                "start": trade_days[0] if trade_days else None,
                "end": trade_days[-1] if trade_days else None,
            }
        )

    series, prefetch_errors = prefetch_series(
        symbols, max_workers=max_workers, on_progress=on_progress
    )
    bench = load_benchmark(end)
    if bench is None or bench.empty:
        raise RuntimeError("基准日线不可用")

    ticker_index = {item.ticker: item for item in series.values()}

    with _WRITE_LOCK:
        if reset:
            graph_store.reset_memory(owner)
            # Also drop Mongo doc so load starts clean.
            try:
                from ...db import get_db

                get_db().signal_graph_state.delete_one(
                    {"_id": f"signal_graph:{owner}"}
                )
            except Exception:
                pass

        graph, ledger, meta = graph_store.load_runtime(owner)
        if reset:
            # ensure empty
            from .a_share_graph.feedback import PredictionLedger
            from .a_share_graph.graph import SignalGraph

            graph = SignalGraph()
            ledger = PredictionLedger()
            meta = {"owner": owner, "tick_by_date": {}, "date_by_tick": {}}

        engine = SignalEngine(graph, ledger)
        feedback = FeedbackEngine(graph, ledger)

        signal_count = 0
        settle_count = 0
        unresolved_count = 0
        gen_errors = 0

        for i, day in enumerate(trade_days):
            tick, meta = resolve_trade_tick(meta, day)
            regime = _regime_from_bench(bench, day)

            for item in series.values():
                try:
                    inputs = _offline_inputs(
                        item,
                        trade_date=day,
                        trade_tick=tick,
                        horizon_days=horizon,
                        owner=owner,
                        bench=bench,
                        market_regime=regime,
                    )
                    if inputs is None:
                        continue
                    decision = engine.generate(
                        inputs["context"],
                        inputs["market_state"],
                        entry_price=inputs["entry_price"],
                        benchmark_entry_price=inputs["benchmark_entry_price"],
                    )
                    if decision.prediction_id:
                        signal_count += 1
                except Exception:
                    gen_errors += 1

            # Settle anything due on this tick.
            due = [
                p
                for p in list(ledger.pending.values())
                if int(p.due_tick) <= int(tick)
            ]
            for prediction in due:
                exit_date = date_for_tick(meta, int(prediction.due_tick)) or day
                item = ticker_index.get(prediction.context.ticker)
                if item is None:
                    try:
                        feedback.mark_unresolved(prediction.prediction_id)
                        unresolved_count += 1
                    except Exception:
                        pass
                    continue
                prices = _exit_from_series(item, bench, exit_date)
                if prices is None:
                    try:
                        feedback.mark_unresolved(prediction.prediction_id)
                        unresolved_count += 1
                    except Exception:
                        pass
                    continue
                try:
                    feedback.settle(
                        prediction.prediction_id,
                        current_tick=tick,
                        stock_exit=prices[0],
                        benchmark_exit=prices[1],
                    )
                    settle_count += 1
                except Exception:
                    try:
                        feedback.mark_unresolved(prediction.prediction_id)
                        unresolved_count += 1
                    except Exception:
                        pass

            if persist_every > 0 and ((i + 1) % persist_every == 0 or i == len(trade_days) - 1):
                graph_store.save_runtime(owner, graph, ledger, meta)

            if on_progress:
                on_progress(
                    {
                        "phase": "replay",
                        "day_index": i + 1,
                        "day_total": len(trade_days),
                        "trade_date": day,
                        "trade_tick": tick,
                        "market_regime": regime,
                        "signal_count": signal_count,
                        "settle_count": settle_count,
                        "pending": len(ledger.pending),
                        "edges": len(graph.edges),
                    }
                )

        summary = graph_store.save_runtime(owner, graph, ledger, meta)

    return {
        "ok": True,
        "owner": owner,
        "days": len(trade_days),
        "start": trade_days[0] if trade_days else None,
        "end": trade_days[-1] if trade_days else None,
        "universe_symbols": len(symbols),
        "series_ok": len(series),
        "prefetch_errors": len(prefetch_errors),
        "prefetch_error_samples": prefetch_errors[:20],
        "signal_count": signal_count,
        "settle_count": settle_count,
        "unresolved_count": unresolved_count,
        "gen_errors": gen_errors,
        "elapsed_sec": round(time.time() - t0, 1),
        "summary": {**summary, **graph_store.summary(owner)},
    }
