from app.advisor.llm_providers import (
    PROVIDERS,
    SLOT_IDS,
    compute_default_model,
    default_enabled_models,
    intersect_enabled,
)


def test_catalog_keys():
    assert set(PROVIDERS) == {"deepseek", "kimi", "qwen"}
    assert PROVIDERS["deepseek"]["base_url"] == "https://api.deepseek.com"
    assert PROVIDERS["kimi"]["base_url"] == "https://api.moonshot.cn/v1"
    assert PROVIDERS["qwen"]["base_url"] == (
        "https://dashscope.aliyuncs.com/compatible-mode/v1"
    )
    assert len(SLOT_IDS) == 8
    assert "committee_quick" in SLOT_IDS


def test_default_enabled_intersection_and_fallback():
    ids = ["qwen3.7-plus", "other-model", "qwen3.8-max"]
    enabled = default_enabled_models("qwen", ids)
    assert enabled == ["qwen3.7-plus", "qwen3.8-max"]
    assert default_enabled_models("qwen", ["only-a", "only-b"]) == [
        "only-a",
        "only-b",
    ]
    assert default_enabled_models("qwen", ["only-a", "only-b", "only-c", "only-d"]) == [
        "only-a",
        "only-b",
        "only-c",
    ]


def test_compute_default_model():
    assert compute_default_model("deepseek", ["deepseek-v4-pro", "deepseek-v4-flash"]) == (
        "deepseek-v4-flash"
    )
    assert compute_default_model("deepseek", ["deepseek-v4-pro"]) == "deepseek-v4-pro"


def test_intersect_keeps_enabled_order():
    assert intersect_enabled(
        ["b", "a", "gone"],
        ["a", "b", "c"],
    ) == ["b", "a"]
