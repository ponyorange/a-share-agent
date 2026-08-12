"""Structured LLM decision for paper trader cycles."""

from __future__ import annotations

import json
import logging
import math
import re
from typing import Any

from .defaults import default_paper_trader_config

logger = logging.getLogger(__name__)

_SYSTEM = """你是 A 股模拟盘短线交易员。根据候选标的方向标签、持仓与行情，
输出结构化交易意图。只输出一个 JSON 对象，不要 Markdown。
side 只能是 buy、sell、hold。hold 表示不操作。
JSON 形状：
{"actions":[{"symbol":"6位代码","side":"buy|sell|hold","qty":0,"target_weight":null,"reason":"..."}]}
禁止候选池外的标的。qty 与 target_weight 至少提供一种非 hold 意图。
"""


def parse_decision_response(text: str) -> dict[str, Any]:
    raw = (text or "").strip()
    if not raw:
        raise ValueError("empty_llm_response")
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw, re.I)
    if fence:
        raw = fence.group(1).strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"\{[\s\S]*\}", raw)
        if not m:
            raise ValueError("json_not_found") from None
        data = json.loads(m.group(0))
    if not isinstance(data, dict):
        raise ValueError("json_not_object")
    actions = data.get("actions")
    if actions is None:
        data["actions"] = []
    elif not isinstance(actions, list):
        raise ValueError("actions_not_list")
    return data


def normalize_actions(
    actions: list[Any],
    *,
    candidates: list[dict[str, Any]],
    mode: str,
    equity: float,
    quotes: dict[str, dict[str, Any]],
    lot_size: int = 100,
) -> list[dict[str, Any]]:
    pool = {str(c.get("symbol")): c for c in candidates if c.get("symbol")}
    mode_v = (mode or "signal_first").strip()
    out: list[dict[str, Any]] = []
    for row in actions or []:
        if not isinstance(row, dict):
            continue
        symbol = str(row.get("symbol") or "").strip()
        side = str(row.get("side") or "").strip().lower()
        if side == "hold" or not symbol:
            continue
        if side not in ("buy", "sell"):
            continue
        cand = pool.get(symbol)
        if cand is None:
            continue
        if mode_v == "signal_first":
            direction = str(cand.get("direction") or "neutral")
            if side == "buy" and direction != "buy":
                continue
            if side == "sell" and direction != "sell":
                # Allow sell of held when direction sell OR held with sell tag only
                continue
        qty = row.get("qty")
        try:
            qty_f = float(qty) if qty is not None else 0.0
        except (TypeError, ValueError):
            qty_f = 0.0
        if qty_f <= 0:
            tw = row.get("target_weight")
            try:
                w = float(tw) if tw is not None else 0.0
            except (TypeError, ValueError):
                w = 0.0
            px = (quotes.get(symbol) or {}).get("price")
            try:
                price = float(px) if px is not None else 0.0
            except (TypeError, ValueError):
                price = 0.0
            if w > 0 and price > 0 and equity > 0:
                qty_f = math.floor(equity * w / price / lot_size) * lot_size
        if qty_f <= 0:
            continue
        intent = {
            "symbol": symbol,
            "side": side,
            "qty": float(qty_f),
            "reason": str(row.get("reason") or "")[:200],
        }
        out.append(intent)
    return out


def _build_user_prompt(
    *,
    mode: str,
    candidates: list[dict[str, Any]],
    account: dict[str, Any],
    quotes: dict[str, dict[str, Any]],
    nudge: bool,
) -> str:
    lines = [
        f"mode={mode}",
        f"cash={account.get('cash')} equity={account.get('equity')}",
        "candidates:",
    ]
    for c in candidates:
        sym = c["symbol"]
        q = quotes.get(sym) or {}
        lines.append(
            f"- {sym} dir={c.get('direction')} score={c.get('rule_score')} "
            f"graph={c.get('graph_action')} held={c.get('held_qty')} "
            f"price={q.get('price')} chg={q.get('day_chg_pct')}"
        )
    if nudge:
        lines.append(
            "提示：最近多轮零成交，允许对已打买向/卖向标的小仓试错，仍须合理仓位。"
        )
    lines.append("请输出 JSON。")
    return "\n".join(lines)


def run_llm_decide(
    user_id: str,
    *,
    mode: str,
    candidates: list[dict[str, Any]],
    account: dict[str, Any],
    quotes: dict[str, dict[str, Any]],
    nudge: bool = False,
) -> dict[str, Any]:
    cfg = default_paper_trader_config()
    lot = int((cfg.get("risk") or {}).get("lot_size") or 100)
    try:
        equity = float(account.get("equity") or 0)
    except (TypeError, ValueError):
        equity = 0.0

    try:
        from ..agent.llm import build_chat_model
        from ..llm_settings import resolve_llm_credentials

        resolve_llm_credentials(user_id)
        model = build_chat_model(user_id, temperature=0.2, streaming=False)
        prompt = _build_user_prompt(
            mode=mode,
            candidates=candidates,
            account=account,
            quotes=quotes,
            nudge=nudge,
        )
        msg = model.invoke(
            [
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": prompt},
            ]
        )
        text = getattr(msg, "content", None)
        if isinstance(text, list):
            text = "".join(
                str(x.get("text") if isinstance(x, dict) else x) for x in text
            )
        raw = str(text or "")
        parsed = parse_decision_response(raw)
        actions = normalize_actions(
            list(parsed.get("actions") or []),
            candidates=candidates,
            mode=mode,
            equity=equity,
            quotes=quotes,
            lot_size=lot,
        )
        return {"actions": actions, "raw": raw}
    except Exception as exc:
        logger.warning("paper trader llm decide failed: %s", exc)
        return {
            "actions": [],
            "raw": "",
            "error": f"{type(exc).__name__}: {exc}",
        }
