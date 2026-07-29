"""Pydantic models for monitor jobs."""

from __future__ import annotations

from typing import Any, Literal, Self

from pydantic import BaseModel, Field, field_validator, model_validator

from .rules import RULE_TYPES

Scope = Literal["watchlist", "portfolio", "symbols"]
JobKind = Literal["watch", "run_at"]
JobRepeat = Literal["once", "recurring"]
JobCalendar = Literal["trading_days", "everyday"]
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
    scope: Scope = "watchlist"
    symbols: list[str] = Field(default_factory=list)
    rules: list[MonitorRuleIn] = Field(default_factory=list)
    note: str | None = None
    cooldown_sec: int | None = None
    llm_enabled: bool = False
    llm_interval_sec: int | None = None
    llm_anomaly_abs_chg: float | None = None
    knowledge_ids: list[str] = Field(default_factory=list)
    kind: JobKind = "watch"
    repeat: JobRepeat = "recurring"
    calendar: JobCalendar = "trading_days"
    anchor_date: str | None = None
    run_time: str | None = None
    end_time: str | None = "15:05"
    prompt: str | None = None

    @field_validator("title")
    @classmethod
    def _strip_title(cls, v: str) -> str:
        s = v.strip()
        if not s:
            raise ValueError("标题不能为空")
        return s

    @field_validator("anchor_date")
    @classmethod
    def _anchor(cls, v: str | None) -> str | None:
        if v is None or not str(v).strip():
            return None
        text = str(v).strip()[:10]
        # validate format
        from datetime import date

        date.fromisoformat(text)
        return text

    @field_validator("run_time", "end_time")
    @classmethod
    def _hhmm(cls, v: str | None) -> str | None:
        if v is None or not str(v).strip():
            return None
        text = str(v).strip()
        parts = text.split(":")
        if len(parts) < 2:
            raise ValueError("时间格式应为 HH:MM")
        h, m = int(parts[0]), int(parts[1])
        if not (0 <= h <= 23 and 0 <= m <= 59):
            raise ValueError("时间格式应为 HH:MM")
        return f"{h:02d}:{m:02d}"

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

    @model_validator(mode="after")
    def _check_kind(self) -> Self:
        if self.kind == "run_at":
            if not (self.prompt or "").strip():
                raise ValueError("定点任务需要 prompt")
            if not self.run_time:
                raise ValueError("定点任务需要 run_time（如 09:00）")
            if self.repeat == "once" and not self.anchor_date:
                raise ValueError("一次性定点任务需要 anchor_date")
        else:
            if not self.rules and not self.llm_enabled:
                raise ValueError("盯盘任务需要至少一条规则，或开启 LLM 看盘")
            if self.repeat == "once" and not self.anchor_date:
                raise ValueError("一次性盯盘需要 anchor_date（目标交易日）")
        return self


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
