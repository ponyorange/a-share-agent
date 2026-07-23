import pytest

from app.advisor.committee.prompts import (
    COMMON_RULES,
    OUTPUT_FIELDS,
    ROLE_INSTRUCTIONS,
    build_role_prompt,
)


@pytest.mark.parametrize("role", ROLE_INSTRUCTIONS)
def test_each_role_output_fields_start_with_chat_message(role):
    assert OUTPUT_FIELDS[role].startswith("字段：chat_message(")


def test_common_rules_require_safe_chat_message_as_first_json_field():
    assert "JSON 对象首字段必须是 chat_message" in COMMON_RULES
    assert "面向用户的简洁发言" in COMMON_RULES
    assert "内部推理" in COMMON_RULES
    assert "prompt" in COMMON_RULES
    assert "堆栈" in COMMON_RULES


@pytest.mark.parametrize("role", ROLE_INSTRUCTIONS)
def test_rendered_role_prompt_places_chat_message_before_structured_fields(role):
    prompt = build_role_prompt(role, {"evidence": []})
    fields = OUTPUT_FIELDS[role]

    assert fields in prompt
    assert fields.index("chat_message") < fields.index(
        {
            "fundamental": "thesis",
            "technical": "thesis",
            "news": "thesis",
            "quant": "thesis",
            "bull": "argument",
            "bear": "argument",
            "trader": "trade_proposals",
            "chair": "action",
        }[role]
    )
