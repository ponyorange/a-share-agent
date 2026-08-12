"""Default paper trader config from YAML with hard-coded fallback."""

from __future__ import annotations

import copy
from typing import Any

_FALLBACK: dict[str, Any] = {
    "interval_sec": 600,
    "candidate_limit": 40,
    "llm_timeout_sec": 60,
    "cycle_timeout_sec": 120,
    "zero_fill_nudge_rounds": 3,
    "llm_fail_halt_threshold": 5,
    "max_sessions_per_tick": 3,
    "risk": {
        "max_single_position": 0.25,
        "max_total_exposure": 0.90,
        "max_positions": 10,
        "max_trades_per_day": 30,
        "max_daily_loss_pct": 0.05,
        "lot_size": 100,
        "block_limit_board": True,
    },
}


def default_paper_trader_config() -> dict[str, Any]:
    """Deep-copy `paper_trader` from advisor config, else literal fallback."""
    try:
        from ..config_loader import load_config

        raw = load_config().get("paper_trader")
    except Exception:
        raw = None
    if not isinstance(raw, dict):
        return copy.deepcopy(_FALLBACK)
    out = copy.deepcopy(_FALLBACK)
    for key, value in raw.items():
        if key == "risk" and isinstance(value, dict):
            out["risk"] = {**out["risk"], **copy.deepcopy(value)}
        else:
            out[key] = copy.deepcopy(value)
    return out
