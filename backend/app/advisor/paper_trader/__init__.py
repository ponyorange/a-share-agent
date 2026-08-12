"""Autonomous paper-trading agent (MVP)."""

from .defaults import default_paper_trader_config
from .risk import filter_intents, is_near_limit_board, should_halt_for_daily_loss

__all__ = [
    "default_paper_trader_config",
    "filter_intents",
    "is_near_limit_board",
    "should_halt_for_daily_loss",
]
