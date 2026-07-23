"""Next-day signal validation: event-study hit rate + optional AKQuant equity metrics."""

from __future__ import annotations

import time
from typing import Any

import numpy as np
import pandas as pd

from .config_loader import load_config
from .features import FeatureResult, compute_factors, fetch_daily_df, load_benchmark
from .scoring import score_features
from .universe import iter_build_universe_events

_cache: dict[str, Any] = {"ts": 0.0, "data": None}


def _akquant_available() -> bool:
    try:
        import akquant  # noqa: F401

        return True
    except ImportError:
        return False


def _score_at_index(
    df: pd.DataFrame,
    bench_df: pd.DataFrame | None,
    idx: int,
) -> float | None:
    if idx < 24:
        return None
    window = df.iloc[: idx + 1].copy()
    bench_cut = None
    if bench_df is not None and not bench_df.empty:
        bench_cut = bench_df[bench_df["time"] <= window.iloc[-1]["time"]]
    factors = compute_factors(window, bench_cut)
    feat = FeatureResult(
        symbol="x",
        name="x",
        as_of=str(window.iloc[-1]["time"]),
        close=float(window.iloc[-1]["close"]),
        factors=factors,
        bars_used=len(window),
    )
    score, _, _ = score_features(feat)
    return score


def event_study_symbol(
    symbol: str,
    lookback: int,
    threshold: float,
    bench_df: pd.DataFrame | None,
    sample_step: int = 1,
) -> dict[str, Any] | None:
    try:
        name, df = fetch_daily_df(symbol)
    except Exception:
        return None
    if df is None or len(df) < 60:
        return None
    if lookback and len(df) > lookback + 5:
        df = df.iloc[-(lookback + 5) :].reset_index(drop=True)

    step = max(1, int(sample_step or 1))
    signals: list[dict[str, Any]] = []
    for i in range(24, len(df) - 1, step):
        score = _score_at_index(df, bench_df, i)
        if score is None or score < threshold:
            continue
        c0 = float(df.iloc[i]["close"])
        c1 = float(df.iloc[i + 1]["close"])
        if c0 <= 0:
            continue
        ret = c1 / c0 - 1.0
        signals.append(
            {
                "date": str(df.iloc[i]["time"]),
                "score": score,
                "next_ret": ret,
                "hit": ret > 0,
            }
        )

    if not signals:
        return {
            "symbol": symbol,
            "name": name,
            "n_signals": 0,
            "hit_rate": None,
            "avg_next_ret": None,
            "engine": "event_study",
        }

    hits = sum(1 for s in signals if s["hit"])
    rets = [s["next_ret"] for s in signals]
    return {
        "symbol": symbol,
        "name": name,
        "n_signals": len(signals),
        "hit_rate": round(hits / len(signals), 4),
        "avg_next_ret": round(float(np.mean(rets)), 6),
        "engine": "event_study",
    }


def _precompute_scores(
    df: pd.DataFrame,
    bench_df: pd.DataFrame | None,
    threshold: float,
) -> pd.DataFrame:
    scores: list[float] = []
    for i in range(len(df)):
        s = _score_at_index(df, bench_df, i)
        scores.append(0.0 if s is None else float(s))
    out = df.copy()
    out["score"] = scores
    out["signal"] = (out["score"] >= threshold).astype(float)
    return out


def run_akquant_symbol(
    symbol: str,
    df: pd.DataFrame,
    bench_df: pd.DataFrame | None,
    threshold: float,
) -> dict[str, Any] | None:
    """Hold overnight on high-score days via AKQuant (T+1)."""
    try:
        import akquant as aq
    except ImportError:
        return None

    scored = _precompute_scores(df, bench_df, threshold)
    adf = scored.rename(columns={"time": "datetime"}).copy()
    adf["symbol"] = symbol

    class NextDaySignalStrategy(aq.Strategy):
        def on_bar(self, bar):  # type: ignore[no-untyped-def]
            extra = getattr(bar, "extra", None) or {}
            score = 0.0
            if isinstance(extra, dict):
                score = float(extra.get("score", 0) or 0)
            if score == 0:
                score = float(getattr(bar, "score", 0) or 0)
            signal = score >= threshold
            if not signal and isinstance(extra, dict):
                signal = float(extra.get("signal", 0) or 0) >= 0.5

            pos = self.get_position(bar.symbol)
            qty = float(getattr(pos, "quantity", 0) or 0) if pos else 0.0
            if qty > 0:
                try:
                    self.sell(bar.symbol, int(qty))
                except Exception:
                    pass
            if signal:
                try:
                    self.buy(bar.symbol, 100)
                except Exception:
                    pass

    try:
        result = aq.run_backtest(
            data=adf,
            strategy=NextDaySignalStrategy,
            symbols=symbol,
            initial_cash=100_000.0,
            t_plus_one=True,
            show_progress=False,
        )
    except Exception as exc:
        return {"symbol": symbol, "error": str(exc)}

    metrics: dict[str, Any] = {}
    try:
        mdf = result.metrics_df
        if mdf is not None and not mdf.empty:
            for idx, row in mdf.iterrows():
                metrics[str(idx)] = row.iloc[0]
    except Exception:
        pass

    def _num(key: str) -> float | None:
        v = metrics.get(key)
        try:
            return float(v)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None

    return {
        "symbol": symbol,
        "total_return_pct": _num("total_return_pct"),
        "max_drawdown_pct": _num("max_drawdown_pct"),
        "sharpe_ratio": _num("sharpe_ratio"),
        "execution_count": _num("execution_count"),
        "engine": "akquant",
    }


def run_backtest_summary(
    symbols: list[str] | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """同步回测；内部走流式生成器，取最终 done 事件。"""
    result = None
    for ev in iter_backtest_summary_events(symbols=symbols, force=force):
        if ev["event"] == "done":
            result = ev["data"]
        elif ev["event"] == "error":
            raise RuntimeError(ev["data"].get("detail") or "回测失败")
    if result is None:
        raise RuntimeError("回测未完成")
    return result


def iter_backtest_summary_events(
    symbols: list[str] | None = None,
    force: bool = False,
):
    """SSE：meta → progress* → done。命中缓存时直接 done。"""
    cfg = load_config()
    bt = cfg.get("backtest") or {}
    ttl = float(bt.get("cache_ttl_seconds", 3600))
    lookback = int(bt.get("lookback_bars", 240))
    threshold = float(bt.get("signal_threshold", 0.55))
    sample_step = int(bt.get("sample_step", 5))

    now = time.time()
    if (
        not force
        and _cache["data"] is not None
        and now - float(_cache["ts"]) < ttl
    ):
        yield {
            "event": "meta",
            "data": {"total": 0, "cached": True, "phase": "cache"},
        }
        yield {"event": "done", "data": {**_cache["data"], "from_cache": True}}
        return

    # 先推 meta，避免准备阶段阻塞首包
    yield {
        "event": "meta",
        "data": {
            "total": 0,
            "cached": False,
            "phase": "universe",
            "force": bool(force),
        },
    }

    per_board = int(bt.get("max_symbols_per_board", bt.get("max_symbols", 12)))
    board_ids = [str(b) for b in (bt.get("boards") or ["etf", "hs"])]

    if symbols:
        syms = list(symbols)
        universe = None
    else:
        universe = None
        # force 回测时也复用候选池缓存；仅在无缓存时拉全市场
        for ev in iter_build_universe_events(force=False):
            if ev["event"] == "progress":
                yield {
                    "event": "progress",
                    "data": {
                        **ev["data"],
                        "phase": "universe",
                    },
                }
            elif ev["event"] == "done":
                universe = ev["data"]
            elif ev["event"] == "error":
                yield {"event": "error", "data": ev["data"]}
                return
        if universe is None:
            yield {"event": "error", "data": {"detail": "候选池构建失败"}}
            return

        syms = []
        seen: set[str] = set()
        for bid in board_ids:
            block = (universe.get("boards") or {}).get(bid) or {}
            for u in list(block.get("symbols") or [])[:per_board]:
                s = str(u.get("symbol") or "").strip()
                if len(s) == 6 and s not in seen:
                    seen.add(s)
                    syms.append(s)
        if not syms:
            syms = ["510300", "510500", "159915"]

    aq_syms = syms[:3]
    total_steps = len(syms) + (len(aq_syms) if _akquant_available() else 0)

    yield {
        "event": "meta",
        "data": {
            "total": total_steps,
            "cached": False,
            "phase": "event_study",
            "symbols": len(syms),
            "akquant_symbols": len(aq_syms) if _akquant_available() else 0,
            "boards": board_ids,
            "universe_source": None if not universe else universe.get("source"),
        },
    }

    try:
        bench_df = load_benchmark()
    except Exception as exc:
        yield {"event": "error", "data": {"detail": f"基准加载失败: {exc}"}}
        return

    yield {
        "event": "progress",
        "data": {
            "done": 0,
            "total": total_steps,
            "phase": "event_study",
            "symbol": None,
            "ok": True,
        },
    }
    per_symbol: list[dict[str, Any]] = []
    done = 0
    for sym in syms:
        row = event_study_symbol(
            sym, lookback, threshold, bench_df, sample_step=sample_step
        )
        if row and row.get("n_signals", 0) > 0:
            per_symbol.append(row)
        done += 1
        yield {
            "event": "progress",
            "data": {
                "done": done,
                "total": total_steps,
                "phase": "event_study",
                "symbol": sym,
                "name": (row or {}).get("name"),
                "ok": bool(row and row.get("n_signals", 0) > 0),
            },
        }

    total_signals = sum(r["n_signals"] for r in per_symbol)
    if total_signals > 0:
        hit_rate = (
            sum((r["hit_rate"] or 0) * r["n_signals"] for r in per_symbol)
            / total_signals
        )
        avg_ret = (
            sum((r["avg_next_ret"] or 0) * r["n_signals"] for r in per_symbol)
            / total_signals
        )
    else:
        hit_rate = None
        avg_ret = None

    akquant_rows: list[dict[str, Any]] = []
    if _akquant_available():
        for sym in aq_syms:
            try:
                _, df = fetch_daily_df(sym)
                if lookback and len(df) > lookback + 5:
                    df = df.iloc[-(lookback + 5) :].reset_index(drop=True)
                row = run_akquant_symbol(sym, df, bench_df, threshold)
                if row and "error" not in row:
                    akquant_rows.append(row)
                elif row:
                    akquant_rows.append(row)
            except Exception as exc:
                akquant_rows.append({"symbol": sym, "error": str(exc)})
            done += 1
            yield {
                "event": "progress",
                "data": {
                    "done": done,
                    "total": total_steps,
                    "phase": "akquant",
                    "symbol": sym,
                    "ok": True,
                },
            }

    aq_dd = None
    aq_ret = None
    aq_sharpe = None
    if akquant_rows:
        dds = [
            r["max_drawdown_pct"]
            for r in akquant_rows
            if r.get("max_drawdown_pct") is not None
        ]
        rets = [
            r["total_return_pct"]
            for r in akquant_rows
            if r.get("total_return_pct") is not None
        ]
        shs = [
            r["sharpe_ratio"] for r in akquant_rows if r.get("sharpe_ratio") is not None
        ]
        if dds:
            aq_dd = round(float(np.mean(dds)), 4)
        if rets:
            aq_ret = round(float(np.mean(rets)), 4)
        if shs:
            aq_sharpe = round(float(np.mean(shs)), 4)

    result = {
        "as_of": time.strftime("%Y-%m-%d %H:%M:%S"),
        "threshold": threshold,
        "lookback_bars": lookback,
        "symbols_tested": len(per_symbol),
        "n_signals": total_signals,
        "hit_rate": None if hit_rate is None else round(float(hit_rate), 4),
        "avg_next_ret": None if avg_ret is None else round(float(avg_ret), 6),
        "max_drawdown_approx": None if aq_dd is None else round(aq_dd / 100.0, 6),
        "akquant_avg_return_pct": aq_ret,
        "akquant_avg_max_drawdown_pct": aq_dd,
        "akquant_avg_sharpe": aq_sharpe,
        "engine": "akquant+event_study" if _akquant_available() else "event_study",
        "akquant": {
            "akquant_installed": _akquant_available(),
            "symbols": akquant_rows,
        },
        "per_symbol": sorted(
            per_symbol,
            key=lambda x: (x.get("hit_rate") or 0),
            reverse=True,
        )[:30],
        "disclaimer": cfg.get("disclaimer"),
        "from_cache": False,
    }
    _cache["ts"] = time.time()
    _cache["data"] = result
    yield {"event": "done", "data": result}



def hit_rate_for_symbol(
    symbol: str, bench_df: pd.DataFrame | None = None
) -> float | None:
    if _cache["data"] is not None:
        for row in (_cache["data"] or {}).get("per_symbol") or []:
            if row.get("symbol") == symbol:
                return row.get("hit_rate")

    cfg = load_config()
    bt = cfg.get("backtest") or {}
    bench = bench_df if bench_df is not None else load_benchmark()
    row = event_study_symbol(
        symbol,
        int(bt.get("lookback_bars", 240)),
        float(bt.get("signal_threshold", 0.55)),
        bench,
        sample_step=int(bt.get("sample_step", 5)),
    )
    return None if not row else row.get("hit_rate")
