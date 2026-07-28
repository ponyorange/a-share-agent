"""Pydantic models for monitor jobs."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from .rules import RULE_TYPES

Scope = Literal["watchlist", "portfolio", "symbols"]
RuleType = Literal[
    "price_below",
    "price_above",
    "day_chg_below",
    "day_chg_above",
    "flow_spike_in",
    "flow_spike_out",
]


class MonitorRuleIn(BaseModel):
    type: RuleType
    value: float
    hint: str | None = None
    id: str | None = None
    mult: float | None = None
    window_days: int | None = None


class CreateJobBody(BaseModel):
    title: str = Field(min_length=1, max_length=80)
    scope: Scope
    symbols: list[str] = Field(default_factory=list)
    rules: list[MonitorRuleIn] = Field(min_length=1)
    note: str | None = None
    cooldown_sec: int | None = None
    llm_enabled: bool = False
    llm_interval_sec: int | None = None
    llm_anomaly_abs_chg: float | None = None
    knowledge_ids: list[str] = Field(default_factory=list)

    @field_validator("title")
    @classmethod
    def _strip_title(cls, v: str) -> str:
        s = v.strip()
        if not s:
            raise ValueError("标题不能为空")
        return s

    @field_validator("rules")
    @classmethod
    def _check_rules(cls, rules: list[MonitorRuleIn]) -> list[MonitorRuleIn]:
        for r in rules:
            if r.type not in RULE_TYPES:
                raise ValueError(f"不支持的规则类型: {r.type}")
        return rules

    @field_validator("knowledge_ids")
    @classmethod
    def _trim_knowledge(cls, ids: list[str]) -> list[str]:
        seen: list[str] = []
        for raw in ids:
            kid = str(raw or "").strip()
            if not kid or kid in seen:
                continue
            seen.append(kid)
            if len(seen) >= 8:
                break
        return seen


def rule_to_dict(rule: MonitorRuleIn, rule_id: str) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": rule_id,
        "type": rule.type,
        "value": float(rule.value),
        "hint": (rule.hint or None),
    }
    if rule.mult is not None:
        out["mult"] = float(rule.mult)
    if rule.window_days is not None:
        out["window_days"] = int(rule.window_days)
    if str(rule.type).startswith("flow_spike"):
        out.setdefault("mult", 3.0)
        out.setdefault("window_days", 5)
    return out
