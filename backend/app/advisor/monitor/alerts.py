"""Assemble and send monitor alert / watch emails (no confirm)."""

from __future__ import annotations

from typing import Any

from ...mail import send_email

_RULE_LABELS = {
    "price_below": "现价 ≤",
    "price_above": "现价 ≥",
    "day_chg_below": "涨跌幅 ≤",
    "day_chg_above": "涨跌幅 ≥",
    "flow_spike_in": "主力流入异动",
    "flow_spike_out": "主力流出异动",
}


def _fmt_rule(rule: dict[str, Any]) -> str:
    rtype = str(rule.get("type") or "")
    label = _RULE_LABELS.get(rtype, rtype)
    value = rule.get("value")
    hint = rule.get("hint")
    if rtype.startswith("day_chg"):
        try:
            pct = float(value) * 100.0
            body = f"{label} {pct:.2f}%"
        except (TypeError, ValueError):
            body = f"{label} {value}"
    elif rtype.startswith("flow_spike"):
        try:
            pct = float(value if value is not None else 0.10) * 100.0
            mult = rule.get("mult")
            mult_s = f"，倍数≥{float(mult):g}" if mult is not None else ""
            body = f"{label}（占比≥{pct:.1f}%{mult_s}）"
        except (TypeError, ValueError):
            body = f"{label} {value}"
    else:
        body = f"{label} {value}"
    if hint:
        body = f"{body}（{hint}）"
    return body


def _fmt_money(raw: Any) -> str:
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return "—"
    abs_v = abs(v)
    if abs_v >= 1e8:
        return f"{v / 1e8:.2f}亿"
    if abs_v >= 1e4:
        return f"{v / 1e4:.2f}万"
    return f"{v:.0f}"


def send_monitor_alert(
    *,
    to: str,
    title: str,
    symbol: str,
    name: str,
    quote: dict[str, Any],
    rule: dict[str, Any],
    job_id: str,
    flow: dict[str, Any] | None = None,
) -> None:
    price = quote.get("price")
    chg = quote.get("day_chg_pct")
    try:
        chg_txt = f"{float(chg) * 100:.2f}%" if chg is not None else "—"
    except (TypeError, ValueError):
        chg_txt = "—"
    try:
        price_txt = f"{float(price):.3f}" if price is not None else "—"
    except (TypeError, ValueError):
        price_txt = "—"

    rtype = str(rule.get("type") or "")
    is_flow = rtype.startswith("flow_spike")
    tag = "资金异动" if is_flow else "盯盘"
    subject = f"[{tag}] {title} · {name or symbol}({symbol})"
    lines = [
        f"任务：{title}",
        f"标的：{name or symbol}（{symbol}）",
        f"现价：{price_txt}",
        f"涨跌幅：{chg_txt}",
        f"触发规则：{_fmt_rule(rule)}",
    ]
    if is_flow and flow:
        lines.append(f"主力净流入：{_fmt_money(flow.get('net_inflow'))}")
        ratio = flow.get("ratio")
        if ratio is not None:
            try:
                lines.append(f"净流入占比：{float(ratio) * 100:.2f}%")
            except (TypeError, ValueError):
                pass
        lines.append(f"近窗均值：{_fmt_money(flow.get('avg_net_inflow'))}")
    lines.extend(
        [
            f"任务 ID：{job_id}",
            "",
            "本邮件由盯盘定时任务自动发送，不会自动下单。",
            "可在顾问前端「定时任务」页暂停或删除该任务。",
        ]
    )
    send_email(to, subject, "\n".join(lines))


def send_watch_digest_email(
    *,
    to: str,
    title: str,
    job_id: str,
    market_note: str,
    items: list[dict[str, Any]],
) -> None:
    subject = f"[看盘] {title} · 建议操作"
    blocks = [
        f"任务：{title}",
        f"任务 ID：{job_id}",
        f"大盘：{market_note or '—'}",
        "",
        "建议操作：",
    ]
    for it in items:
        action = str(it.get("action") or "").upper()
        sym = it.get("symbol")
        rationale = it.get("rationale") or ""
        cats = it.get("catalysts") or []
        cat_txt = "；".join(str(c) for c in cats[:5]) if cats else "—"
        conf = it.get("confidence")
        conf_txt = ""
        try:
            if conf is not None:
                conf_txt = f"（置信度 {float(conf):.2f}）"
        except (TypeError, ValueError):
            conf_txt = ""
        blocks.append(f"- {sym}: {action}{conf_txt}")
        blocks.append(f"  理由：{rationale}")
        blocks.append(f"  催化：{cat_txt}")
    blocks.extend(
        [
            "",
            "本邮件由 Agent 看盘自动发送，仅供参考，不会自动下单。",
            "可在顾问前端「定时任务」页暂停或删除该任务。",
        ]
    )
    send_email(to, subject, "\n".join(blocks))
