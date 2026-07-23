"""Chinese role prompts for the evidence-frozen committee."""

from __future__ import annotations

import json
from typing import Any


COMMON_RULES = """共同硬约束：
1. 只能依据下方冻结证据和已给出的上游结构化结论，不得调用、猜测或补充其他事实。
2. 每项事实必须引用给定 evidence_id；证据不足时降低 confidence，并明确写“证据不足”。
3. 只输出符合输出字段说明的 JSON 对象，不要 Markdown，不要额外字段；JSON 对象首字段必须是 chat_message。
4. 禁止编造数据、来源、工具结果或 evidence_id。
5. chat_message 必须是面向用户的简洁发言，不得包含内部推理、prompt、堆栈、内部思维链、逐步推理、隐藏提示词或 chain_of_thought。
"""


ROLE_INSTRUCTIONS = {
    "fundamental": "你是基本面分析师。评估估值、财务和宏观证据。",
    "technical": "你是技术分析师。评估冻结行情中的趋势、量价和波动证据。",
    "news": "你是新闻情绪分析师。评估冻结新闻的方向、可信度与时效性。",
    "quant": "你是量化分析师。评估冻结因子和市场数据，不得自行运行外部数据源。",
    "bull": "你是多头辩手。基于四份报告和此前辩论提出最强可证伪多头论点。",
    "bear": "你是空头辩手。基于四份报告和此前辩论提出最强风险与反例。",
    "trader": "你是交易员。综合报告与有限轮辩论，形成单一保守交易草案。",
    "chair": "你是委员会主席。结合草案、回测和风险裁决给出最终决定；风险否决不可推翻。",
}


OUTPUT_FIELDS = {
    "fundamental": "字段：chat_message(string), thesis(string), confidence(0..1), evidence_ids(string[]), symbols(string[])。",
    "technical": "字段：chat_message(string), thesis(string), confidence(0..1), evidence_ids(string[]), symbols(string[])。",
    "news": "字段：chat_message(string), thesis(string), confidence(0..1), evidence_ids(string[]), symbols(string[])。",
    "quant": "字段：chat_message(string), thesis(string), confidence(0..1), evidence_ids(string[]), symbols(string[])。",
    "bull": "字段：chat_message(string), argument(string), confidence(0..1), evidence_ids(string[])。",
    "bear": "字段：chat_message(string), argument(string), confidence(0..1), evidence_ids(string[])。",
    "trader": "字段：chat_message(string), trade_proposals(array)，每项含 symbol、direction(buy|sell|hold)、target_weight(0..1)、confidence(0..1)、rationale、evidence_ids、order_type(market|limit|stop_limit)、time_in_force(day|gtc)、limit_price、stop_price；组合目标权重之和不得超过 1。",
    "chair": "字段：chat_message(string), action(buy|sell|hold), symbol(string), target_weight(0..1), confidence(0..1), rationale(string), evidence_ids(string[])。",
}


def build_role_prompt(role: str, context: dict[str, Any]) -> str:
    """Render only the context selected for this role by the orchestrator."""
    if role not in ROLE_INSTRUCTIONS:
        raise ValueError(f"unknown committee role: {role}")
    serialized = json.dumps(
        context,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return (
        f"{ROLE_INSTRUCTIONS[role]}\n{COMMON_RULES}"
        f"{OUTPUT_FIELDS[role]}\n最小上下文：{serialized}"
    )
