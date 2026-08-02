from __future__ import annotations

from collections import Counter
from typing import Any


def _regime_cfg(cfg: dict | None) -> dict:
    if cfg is not None:
        return cfg
    from ..config_loader import load_config

    return load_config().get("regime") or {}


def _board_histogram(sealed: list[dict]) -> Counter[int]:
    return Counter(int(item.get("board_count") or 0) for item in sealed)


def _cycle_from_score(score: float, thresholds: dict[str, float]) -> str:
    if score >= thresholds.get("climax", 0.75):
        return "climax"
    if score >= thresholds.get("strengthen", 0.55):
        return "ebb"
    if score >= thresholds.get("repair", 0.35):
        return "strengthen"
    if score >= thresholds.get("ice", 0.20):
        return "repair"
    return "ice"


def _clip01(x: float) -> float:
    return max(0.0, min(1.0, x))


def compute_sentiment_metrics(
    today: dict,
    prev: dict | None,
    cfg: dict | None = None,
) -> dict[str, Any]:
    regime = _regime_cfg(cfg)
    height_min = int(regime.get("height_board_min") or 3)
    weights = regime.get("sentiment_weights") or {}
    thresholds = regime.get("cycle_thresholds") or {}

    sealed = list(today.get("sealed") or [])
    broken = list(today.get("broken") or [])
    limit_down_count = int(today.get("limit_down_count") or 0)

    limit_up_count = len(sealed)
    broken_count = len(broken)
    denom = limit_up_count + broken_count
    evidence: list[dict[str, Any]] = []

    if denom == 0:
        seal_rate = 0.0
        evidence.append(
            {"key": "seal_rate", "value": "0", "note": "无涨停与炸板样本，封板率记为 0"}
        )
    else:
        seal_rate = limit_up_count / denom

    break_rate = 1.0 - seal_rate if denom else 0.0

    boards = [int(x.get("board_count") or 0) for x in sealed]
    max_board = max(boards) if boards else 0
    height_board_count = sum(1 for b in boards if b >= height_min)

    today_by_board = _board_histogram(sealed)
    promotion_rate: float | None = None

    if prev is None:
        evidence.append(
            {
                "key": "promotion_rate",
                "value": "null",
                "note": "缺少昨日连板归档，晋级率不可用",
            }
        )
    else:
        prev_by_board = {int(k): int(v) for k, v in (prev.get("by_board") or {}).items()}
        prev_first = max(1, prev_by_board.get(1, 0))
        today_second = today_by_board.get(2, 0)
        promotion_rate = today_second / prev_first
        evidence.append(
            {
                "key": "promotion_rate",
                "value": f"{promotion_rate:.4f}",
                "note": f"2连板 {today_second} / 昨首板 {prev_by_board.get(1, 0)}",
            }
        )
        max_k = max([2, *today_by_board.keys(), *[k + 1 for k in prev_by_board.keys()]])
        for k in range(3, max_k + 1):
            today_k = today_by_board.get(k, 0)
            prev_k1 = prev_by_board.get(k - 1, 0)
            if today_k or prev_k1:
                rate_k = today_k / max(1, prev_k1)
                evidence.append(
                    {
                        "key": f"promotion_k{k}",
                        "value": f"{rate_k:.4f}",
                        "note": f"{k}连 {today_k} / 昨{k - 1}连 {prev_k1}",
                    }
                )

    w_seal = float(weights.get("seal_rate", 0.25))
    w_height = float(weights.get("height", 0.25))
    w_prom = float(weights.get("promotion", 0.25))
    w_limit = float(weights.get("limit_up_count", 0.15))
    w_down = float(weights.get("limit_down_penalty", 0.10))

    height_norm = _clip01(max_board / 10.0)
    limit_up_norm = _clip01(limit_up_count / 100.0)
    prom_norm = promotion_rate if promotion_rate is not None else 0.0
    down_norm = _clip01(limit_down_count / 50.0)

    raw_score = (
        w_seal * seal_rate
        + w_height * height_norm
        + w_prom * prom_norm
        + w_limit * limit_up_norm
        - w_down * down_norm
    )
    sentiment_score = _clip01(raw_score)
    sentiment_cycle = _cycle_from_score(sentiment_score, thresholds)

    evidence.extend(
        [
            {"key": "limit_up_count", "value": str(limit_up_count), "note": "涨停家数"},
            {"key": "limit_down_count", "value": str(limit_down_count), "note": "跌停家数"},
            {"key": "seal_rate", "value": f"{seal_rate:.4f}", "note": "封板率"},
            {"key": "max_board", "value": str(max_board), "note": "最高连板"},
            {"key": "sentiment_score", "value": f"{sentiment_score:.4f}", "note": "情绪温度分"},
        ]
    )

    return {
        "limit_up_count": limit_up_count,
        "limit_down_count": limit_down_count,
        "broken_count": broken_count,
        "seal_rate": seal_rate,
        "break_rate": break_rate,
        "max_board": max_board,
        "height_board_count": height_board_count,
        "promotion_rate": promotion_rate,
        "sentiment_score": sentiment_score,
        "sentiment_cycle": sentiment_cycle,
        "evidence": evidence,
    }
