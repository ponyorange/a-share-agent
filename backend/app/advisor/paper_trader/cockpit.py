"""Aggregated read model for the paper trader cockpit UI."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ...quote import trading_session
from ..paper import get_account
from .candidates import build_candidates_light
from .store import get_session, list_decisions

_POSITION_LIMIT = 20


def build_cockpit(
    user_id: str,
    *,
    decisions_page: int = 1,
    decisions_page_size: int = 20,
) -> dict[str, Any]:
    errors: dict[str, str] = {}
    session = get_session(user_id) or {"status": "stopped"}

    paper: dict[str, Any] = {
        "cash": None,
        "equity": None,
        "market_value": None,
        "positions": [],
        "positions_count": 0,
    }
    try:
        acc = get_account(user_id, mark_to_market=False)
        positions = list(acc.get("positions") or [])
        slim = []
        for p in positions[:_POSITION_LIMIT]:
            if not isinstance(p, dict):
                continue
            slim.append(
                {
                    "symbol": p.get("symbol"),
                    "name": p.get("name"),
                    "qty": p.get("qty"),
                    "cost": p.get("cost"),
                    "last": p.get("last"),
                }
            )
        paper = {
            "cash": acc.get("cash"),
            "equity": acc.get("equity"),
            "market_value": acc.get("market_value"),
            "positions": slim,
            "positions_count": len(positions),
        }
    except Exception as exc:
        errors["paper"] = f"{type(exc).__name__}: {exc}"

    candidates: list[dict[str, Any]] = []
    try:
        # Light path only: archived recs + watchlist + positions (fast for UI poll).
        candidates = build_candidates_light(user_id)
    except Exception as exc:
        errors["candidates"] = f"{type(exc).__name__}: {exc}"

    decisions: dict[str, Any] = {
        "page": decisions_page,
        "page_size": decisions_page_size,
        "total": 0,
        "items": [],
    }
    try:
        decisions = list_decisions(
            user_id, page=decisions_page, page_size=decisions_page_size
        )
    except Exception as exc:
        errors["decisions"] = f"{type(exc).__name__}: {exc}"

    ts = trading_session()
    out: dict[str, Any] = {
        "session": session,
        "paper": paper,
        "candidates": candidates,
        "decisions": decisions,
        "meta": {
            "is_trading": bool(ts.get("is_trading")),
            "is_trading_day": bool(ts.get("is_trading_day")),
            "server_now": datetime.now(timezone.utc).isoformat(),
        },
    }
    if errors:
        out["errors"] = errors
    return out
