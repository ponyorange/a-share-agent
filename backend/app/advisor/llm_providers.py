"""Fixed LLM provider catalog and OpenAI-compatible list/ping helpers."""

from __future__ import annotations

from openai import OpenAI

PROVIDER_IDS = ("deepseek", "kimi", "qwen")
SLOT_IDS = (
    "agent",
    "paper",
    "home",
    "monitor",
    "policy",
    "limitup",
    "committee_quick",
    "committee_deep",
)
PROVIDERS: dict[str, dict] = {
    "deepseek": {
        "label": "DeepSeek",
        "base_url": "https://api.deepseek.com",
        "default_model": "deepseek-v4-flash",
        "preselect": ("deepseek-v4-flash", "deepseek-v4-pro"),
        "docs_url": "https://api-docs.deepseek.com/zh-cn/",
    },
    "kimi": {
        "label": "Kimi",
        "base_url": "https://api.moonshot.cn/v1",
        "default_model": "kimi-k2.6",
        "preselect": ("kimi-k2.6", "kimi-k2.7-code", "kimi-k3"),
        "docs_url": "https://platform.kimi.com/docs/api/overview",
    },
    "qwen": {
        "label": "千问",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "default_model": "qwen3.7-plus",
        "preselect": ("qwen3.7-flash", "qwen3.7-plus", "qwen3.8-max"),
        "docs_url": (
            "https://platform.qianwenai.com/docs/developer-guides/"
            "getting-started/text-generation-models"
        ),
    },
}


def default_enabled_models(provider: str, available_ids: list[str]) -> list[str]:
    pre = list(PROVIDERS[provider]["preselect"])
    avail = list(available_ids)
    picked = [mid for mid in pre if mid in avail]
    if picked:
        return picked
    return avail[:3]


def compute_default_model(provider: str, enabled_models: list[str]) -> str:
    if not enabled_models:
        return str(PROVIDERS[provider]["default_model"])
    sys_default = str(PROVIDERS[provider]["default_model"])
    if sys_default in enabled_models:
        return sys_default
    return enabled_models[0]


def intersect_enabled(enabled: list[str], available_ids: list[str]) -> list[str]:
    allow = set(available_ids)
    return [mid for mid in enabled if mid in allow]


def list_model_ids(api_key: str, base_url: str) -> list[str]:
    client = OpenAI(api_key=api_key.strip(), base_url=base_url.rstrip("/"))
    page = client.models.list()
    out: list[str] = []
    for item in page.data:
        mid = str(getattr(item, "id", "") or "").strip()
        if mid:
            out.append(mid)
    return out


def ping_chat(
    api_key: str, base_url: str, model: str, *, provider: str
) -> None:
    client = OpenAI(api_key=api_key.strip(), base_url=base_url.rstrip("/"))
    kwargs: dict = {
        "model": model,
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 8,
    }
    if provider != "kimi":
        kwargs["temperature"] = 0
    client.chat.completions.create(**kwargs)
