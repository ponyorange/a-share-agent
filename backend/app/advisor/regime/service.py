from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timedelta
from time import monotonic
from typing import Any
from zoneinfo import ZoneInfo

from ..calendar_util import is_trading_day, last_trading_day
from ..config_loader import load_config
from . import collector, store
from .sentiment import compute_sentiment_metrics
from .synthesize import synthesize_gate
from .trend import classify_trend

SH = ZoneInfo("Asia/Shanghai")
_CACHE: dict[str, Any] = {}
_CLOCK = lambda: datetime.now(SH)

REGIME_MORNING_BRIEF_PROMPT = (
    "请调用 get_market_regime，用中文输出今日市场状态简报："
    "趋势、情绪周期、闸门、仓位上限、三条证据、对交易的含义。"
    "不要编造点位；需要指数点位时再调 fetch_market_indices。"
)


def _regime_cfg() -> dict[str, Any]:
    return dict((load_config().get("regime") or {}))


def _previous_trading_day(trade_date: str) -> str:
    parsed = date.fromisoformat(str(trade_date)[:10])
    return last_trading_day(parsed - timedelta(days=1))


def _by_board(sealed: list[dict[str, Any]]) -> dict[int, int]:
    return dict(Counter(int(x.get("board_count") or 0) for x in sealed))


def _data_quality(raw: dict[str, Any], metrics: dict[str, Any]) -> str:
    errors = [str(x).lower() for x in (raw.get("errors") or [])]
    if any(x.startswith("sealed:") for x in errors):
        return "failed"
    trend_features = dict(raw.get("trend_features") or {})
    if metrics.get("promotion_rate") is None or not trend_features:
        return "degraded"
    return "ok"


def _quality_evidence(
    quality: str,
    raw: dict[str, Any],
    metrics: dict[str, Any],
) -> list[dict[str, Any]]:
    notes: list[str] = []
    if raw.get("errors"):
        notes.append("; ".join(str(x) for x in raw.get("errors") or []))
    if metrics.get("promotion_rate") is None:
        notes.append("promotion_rate 缺失")
    if not dict(raw.get("trend_features") or {}):
        notes.append("trend_features 缺失")
    return [
        {
            "key": "data_quality",
            "value": quality,
            "note": "；".join(notes) if notes else "输入完整",
        }
    ]


def _apply_failed_gate(cfg: dict[str, Any], out: dict[str, Any]) -> None:
    out["gate_level"] = "defensive"
    out["position_cap"] = float((cfg.get("position_cap") or {}).get("defensive", 0.35))
    out["pool_policy"] = (cfg.get("pool_policy") or {}).get("defensive", "shrink")


def _cycle_after_quality_and_hysteresis(
    metrics: dict[str, Any],
    prev_daily: dict[str, Any] | None,
    quality: str,
    cfg: dict[str, Any],
    evidence: list[dict[str, Any]],
) -> str:
    if quality == "failed":
        previous = (prev_daily or {}).get("sentiment_cycle")
        cycle = str(previous or "repair")
        evidence.append(
            {
                "key": "sentiment_cycle",
                "value": cycle,
                "note": "sealed 数据失败，沿用上一周期；无归档时使用 repair",
            }
        )
        return cycle

    cycle = str(metrics.get("sentiment_cycle") or "repair")
    thresholds = cfg.get("cycle_thresholds") or {}
    strengthen = float(thresholds.get("strengthen", 0.55))
    hysteresis = float(cfg.get("cycle_hysteresis", 0.05))
    ebb_threshold = strengthen - hysteresis
    score = float(metrics.get("sentiment_score") or 0.0)
    if (
        prev_daily
        and score < ebb_threshold
        and (
            prev_daily.get("sentiment_cycle") == "climax"
            or prev_daily.get("gate_level") == "aggressive"
        )
    ):
        evidence.append(
            {
                "key": "sentiment_cycle",
                "value": "ebb",
                "note": (
                    f"前一归档为高潮/进攻，当前温度 {score:.4f} 低于 "
                    f"strengthen−hysteresis（{ebb_threshold:.4f}）"
                ),
            }
        )
        return "ebb"
    return cycle


def build_regime_from_parts(
    raw: dict[str, Any],
    prev_daily: dict[str, Any] | None,
    *,
    cfg: dict[str, Any] | None = None,
    as_of: datetime | None = None,
) -> dict[str, Any]:
    regime_cfg = cfg or _regime_cfg()
    metrics = compute_sentiment_metrics(raw, prev_daily, cfg=regime_cfg)
    trend_features = dict(raw.get("trend_features") or {})
    trend = classify_trend(trend_features, cfg=regime_cfg)
    quality = _data_quality(raw, metrics)
    evidence = [
        *list(trend.get("evidence") or []),
        *list(metrics.get("evidence") or []),
        *_quality_evidence(quality, raw, metrics),
    ]
    sentiment_cycle = _cycle_after_quality_and_hysteresis(
        metrics,
        prev_daily,
        quality,
        regime_cfg,
        evidence,
    )
    gate = synthesize_gate(str(trend["trend_regime"]), sentiment_cycle, cfg=regime_cfg)
    if quality == "failed":
        _apply_failed_gate(regime_cfg, gate)

    now = as_of or _CLOCK()
    out = {
        "as_of": now.isoformat(),
        "trade_date": raw.get("trade_date"),
        "trend_regime": trend["trend_regime"],
        "sentiment_cycle": sentiment_cycle,
        "sentiment_score": metrics["sentiment_score"],
        **gate,
        "data_quality": quality,
        "evidence": evidence,
        "override_allowed": True,
        "metrics": metrics,
        "by_board": _by_board(list(raw.get("sealed") or [])),
    }
    out.update(
        {
            k: v
            for k, v in metrics.items()
            if k not in {"evidence", "sentiment_cycle"}
        }
    )
    return out


def _ensure_previous_archive(trade_date: str, cfg: dict[str, Any]) -> None:
    if not is_trading_day(trade_date):
        return
    prev_date = _previous_trading_day(trade_date)
    if store.get_daily(prev_date):
        return
    prev_prev = store.get_daily(_previous_trading_day(prev_date))
    raw = collector.collect_raw(prev_date)
    archived = build_regime_from_parts(raw, prev_prev, cfg=cfg)
    store.upsert_daily(prev_date, archived)


def get_current_regime(*, force: bool = False) -> dict[str, Any]:
    cfg = _regime_cfg()
    ttl = max(0, int(cfg.get("cache_ttl_seconds") or 0))
    now_mono = monotonic()
    cached = _CACHE.get("current")
    if (
        not force
        and cached
        and ttl > 0
        and now_mono - float(cached.get("ts") or 0.0) < ttl
    ):
        return dict(cached["payload"])

    raw = collector.collect_raw()
    trade_date = str(raw.get("trade_date") or _CLOCK().date().isoformat())
    _ensure_previous_archive(trade_date, cfg)
    prev_daily = store.get_daily(_previous_trading_day(trade_date))
    payload = build_regime_from_parts(raw, prev_daily, cfg=cfg)
    store.upsert_daily(trade_date, payload)
    _CACHE["current"] = {"ts": now_mono, "payload": dict(payload)}
    return payload


def get_regime_history(limit: int = 20) -> list[dict[str, Any]]:
    cfg = _regime_cfg()
    default_limit = int(cfg.get("history_default_limit") or 20)
    n = limit if limit is not None else default_limit
    return store.list_daily(n)


def get_sentiment_detail() -> dict[str, Any]:
    current = get_current_regime()
    return {
        "metrics": current.get("metrics") or {},
        "sentiment_cycle": current.get("sentiment_cycle"),
    }
