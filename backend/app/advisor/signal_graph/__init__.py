"""Shared A-share learnable SignalGraph for advisor surfaces."""

from .evolve import run_daily_evolve
from .service import (
    attach_graph_fields,
    generate_signal,
    generate_signals_batch,
    get_signal_view,
    get_summary,
    settle_due,
    signal_graph_config,
)

__all__ = [
    "attach_graph_fields",
    "generate_signal",
    "generate_signals_batch",
    "get_signal_view",
    "get_summary",
    "run_daily_evolve",
    "settle_due",
    "signal_graph_config",
]
