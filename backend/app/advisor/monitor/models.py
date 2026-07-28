"""Pydantic models for monitor jobs."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from .rules import RULE_TYPES

Scope = Literal["watchlist", "portfolio", "symbols"]
RuleType = Literal["price_below", "price_above", "day_chg_below", "day_chg_above"]


class MonitorRuleIn(BaseModel):
    type: RuleType
    value: float
    hint: str | None = None
    id: str | None = None


class CreateJobBody(BaseModel):
    title: str = Field(min_length=1, max_length=80)
    scope: Scope
    symbols: list[str] = Field(default_factory=list)
    rules: list[MonitorRuleIn] = Field(min_length=1)
    note: str | None = None
    cooldown_sec: int | None = None

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


def rule_to_dict(rule: MonitorRuleIn, rule_id: str) -> dict[str, Any]:
    return {
        "id": rule_id,
        "type": rule.type,
        "value": float(rule.value),
        "hint": (rule.hint or None),
    }
