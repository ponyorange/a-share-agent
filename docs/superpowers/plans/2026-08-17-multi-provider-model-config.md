# 多模型提供方与分模块模型配置 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 Agent 面板改成「模型配置」：DeepSeek / Kimi / 千问各自存 Key，八个功能槽位独立选提供方+模型，模型下拉来自用户勾选的 `/models` 清单。

**Architecture:** 新增 `llm_providers.py` 放提供方目录与 `/models`/ping；`llm_settings.py` 存 `providers` + `slots`，读时迁移旧顶层 DeepSeek 字段。`build_chat_model` 必须带 `slot` 或委员会 `tier`；Kimi 不传 temperature。设置页两层 UI，新 REST 写提供方与槽位。

**Tech Stack:** Python 3.12、OpenAI SDK、langchain_openai.ChatOpenAI、FastAPI、Mongo `user_llm_settings`、React/Vitest（frontend-advisor）

## Global Constraints

- Spec：`docs/superpowers/specs/2026-08-17-multi-provider-model-config-design.md`
- 提供方 ID 只能是 `deepseek` / `kimi` / `qwen`；槽位只能是 `agent` `paper` `home` `monitor` `policy` `limitup` `committee_quick` `committee_deep`
- 用户可见缺 Key 文案统一为「请先在模型配置中填写 API Key」；`resolve` 抛 `尚未配置 API Key，请先在模型配置中填写`
- 不测真实外网 Key；`models.list` 与 chat ping 一律 mock
- 不做自定义 base_url、思考模式开关、更多提供方
- Docker 镜像标签仍为 `名称:架构`（如 `share-data:amd64`），禁止部署默认 `latest`
- 计划中的 commit 步骤默认跳过，除非用户明确要求提交

---

### File map

| 文件 | 职责 |
|------|------|
| `backend/app/advisor/llm_providers.py` | 提供方目录、槽位常量、默认勾选/`default_model` 纯函数、list+ping |
| `backend/app/advisor/llm_settings.py` | 迁移、公开 JSON、存删提供方、刷新模型、槽位/勾选、resolve |
| `backend/app/advisor/agent/llm.py` | `build_chat_model(slot= \| tier=)`；Kimi 省略 temperature |
| `backend/app/advisor/routes.py` | 新 REST + 403 文案 |
| `backend/app/advisor/agent/web_tools.py` | `web_research` 用 DeepSeek Key |
| `backend/app/advisor/agent/graph.py` 等调用点 | 传入对应 `slot` |
| `frontend-advisor/src/agentApi.ts` | 新类型与 API |
| `frontend-advisor/src/pages/AgentSettingsPage.tsx` | 三提供方卡片 + 八槽位 + 联网 |
| `frontend-advisor/src/components/TopbarNav.tsx` 等 | 「模型配置」文案 |
| `backend/tests/test_llm_providers.py` | 目录纯函数 |
| `backend/tests/test_llm_web_settings.py` | 持久化/迁移/槽位（扩展） |
| `backend/tests/test_committee_llm.py` | slot/tier/Kimi |
| `frontend-advisor/src/pages/AgentSettingsPage.test.tsx` | 设置页 |

---

### Task 1: 提供方目录与纯函数

**Files:**
- Create: `backend/app/advisor/llm_providers.py`
- Create: `backend/tests/test_llm_providers.py`

**Interfaces:**
- Produces:
  - `PROVIDER_IDS: tuple[str, ...] = ("deepseek", "kimi", "qwen")`
  - `SLOT_IDS: tuple[str, ...] = ("agent", "paper", "home", "monitor", "policy", "limitup", "committee_quick", "committee_deep")`
  - `PROVIDERS: dict[str, dict]` 每项含 `label`, `base_url`, `default_model`, `preselect: tuple[str, ...]`
  - `compute_default_model(provider: str, enabled_models: list[str]) -> str`
  - `default_enabled_models(provider: str, available_ids: list[str]) -> list[str]`
  - `intersect_enabled(enabled: list[str], available_ids: list[str]) -> list[str]`（保持 enabled 原相对顺序）
  - `list_model_ids(api_key: str, base_url: str) -> list[str]`
  - `ping_chat(api_key: str, base_url: str, model: str, *, provider: str) -> None`（`provider=="kimi"` 不传 temperature）

- [ ] **Step 1: 写失败单测**

```python
# backend/tests/test_llm_providers.py
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
```

- [ ] **Step 2: 跑测确认失败**

Run: `cd backend && python -m pytest tests/test_llm_providers.py -v`  
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现目录与纯函数**

```python
# backend/app/advisor/llm_providers.py
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
```

- [ ] **Step 4: 跑测确认通过**

Run: `cd backend && python -m pytest tests/test_llm_providers.py -v`  
Expected: PASS

- [ ] **Step 5: Commit（默认跳过）**

```bash
git add backend/app/advisor/llm_providers.py backend/tests/test_llm_providers.py
git commit -m "feat: add LLM provider catalog helpers"
```

---

### Task 2: 公开设置形状与旧文档迁移

**Files:**
- Modify: `backend/app/advisor/llm_settings.py`
- Modify: `backend/tests/test_llm_web_settings.py`（`test_public_defaults_without_doc` + 新迁移用例）

**Interfaces:**
- Consumes: Task 1 `PROVIDERS`, `PROVIDER_IDS`, `SLOT_IDS`, `compute_default_model`
- Produces:
  - `MISSING_KEY_MESSAGE = "尚未配置 API Key，请先在模型配置中填写"`
  - `HTTP_MISSING_KEY_DETAIL = "请先在模型配置中填写 API Key"`
  - `ensure_migrated(user_id: str) -> dict`（无文档则 `{}`，有旧顶层 Key 则写回新结构）
  - `public_llm_settings(user_id: str) -> dict` 含 `configured`, `providers`（三家补齐）, `slots`（八键，未配则为 `null`）, 以及现有 Tavily/web 字段。**不再**在顶层返回 `model` / `provider` / `base_url` / `key_hint` / `last_validated_at`

- [ ] **Step 1: 扩展失败单测**

在 `test_llm_web_settings.py` 追加（保留原 FakeColl / `db` fixture）：

```python
def test_public_defaults_without_doc(db):
    pub = llm_settings.public_llm_settings("u1")
    assert pub["configured"] is False
    assert pub["web_research_enabled"] is True
    assert pub["tavily_enabled"] is False
    assert pub["providers"]["deepseek"]["configured"] is False
    assert pub["providers"]["kimi"]["default_model"] == "kimi-k2.6"
    assert pub["providers"]["qwen"]["default_model"] == "qwen3.7-plus"
    assert pub["slots"]["agent"] is None
    assert "model" not in pub


def test_migrates_legacy_top_level_key(db, monkeypatch):
    monkeypatch.setenv(
        "LLM_ENCRYPTION_KEY",
        "unit-test-llm-encryption-key-32bytes-min!!",
    )
    enc = llm_settings.encrypt_api_key("sk-legacy-key-xxxx")
    db.user_llm_settings.docs["u1"] = {
        "user_id": "u1",
        "api_key_enc": enc,
        "key_hint": "sk-l…xxxx",
        "model": "deepseek-v4-pro",
        "provider": "deepseek",
        "base_url": "https://api.deepseek.com",
    }
    pub = llm_settings.public_llm_settings("u1")
    assert pub["configured"] is True
    assert pub["providers"]["deepseek"]["configured"] is True
    assert pub["providers"]["deepseek"]["enabled_models"]  # includes old model
    assert "deepseek-v4-pro" in pub["providers"]["deepseek"]["enabled_models"]
    assert pub["slots"]["agent"] == {
        "provider": "deepseek",
        "model": "deepseek-v4-pro",
    }
    assert pub["slots"]["committee_deep"]["provider"] == "deepseek"
    stored = db.user_llm_settings.docs["u1"]
    assert "api_key_enc" not in stored
    assert stored["providers"]["deepseek"]["api_key_enc"]
```

把原 `test_public_defaults_without_doc` 改成上面第一段（顶层不再有 `model`）。

- [ ] **Step 2: 跑测确认失败**

Run: `cd backend && python -m pytest tests/test_llm_web_settings.py::test_public_defaults_without_doc tests/test_llm_web_settings.py::test_migrates_legacy_top_level_key -v`  
Expected: FAIL（`providers` 缺失 / 未迁移）

- [ ] **Step 3: 实现迁移与新 public 形状**

在 `llm_settings.py` 增加常量与辅助（保留 Fernet / Tavily 逻辑）。要点：

```python
from .llm_providers import (
    PROVIDER_IDS,
    PROVIDERS,
    SLOT_IDS,
    compute_default_model,
)

MISSING_KEY_MESSAGE = "尚未配置 API Key，请先在模型配置中填写"
HTTP_MISSING_KEY_DETAIL = "请先在模型配置中填写 API Key"


def _empty_provider_public(pid: str) -> dict:
    spec = PROVIDERS[pid]
    return {
        "configured": False,
        "key_hint": None,
        "last_validated_at": None,
        "available_models": [],
        "enabled_models": [],
        "default_model": spec["default_model"],
        "models_synced_at": None,
    }


def _provider_public(pid: str, pdata: dict | None) -> dict:
    spec = PROVIDERS[pid]
    d = pdata or {}
    configured = bool(d.get("api_key_enc"))
    out = _empty_provider_public(pid)
    if not configured:
        return out
    out.update(
        {
            "configured": True,
            "key_hint": d.get("key_hint"),
            "last_validated_at": _iso(d.get("last_validated_at")),
            "available_models": list(d.get("available_models") or []),
            "enabled_models": list(d.get("enabled_models") or []),
            "default_model": d.get("default_model") or spec["default_model"],
            "models_synced_at": _iso(d.get("models_synced_at")),
        }
    )
    return out


def _any_provider_configured(doc: dict) -> bool:
    providers = doc.get("providers") or {}
    return any(bool((providers.get(pid) or {}).get("api_key_enc")) for pid in PROVIDER_IDS)


def _migrate_legacy_inplace(doc: dict) -> bool:
    """Return True if mutation happened."""
    providers = dict(doc.get("providers") or {})
    ds = dict(providers.get("deepseek") or {})
    if doc.get("api_key_enc") and not ds.get("api_key_enc"):
        old_model = str(doc.get("model") or PROVIDERS["deepseek"]["default_model"])
        pre = list(PROVIDERS["deepseek"]["preselect"])
        enabled = []
        for mid in [old_model, *pre]:
            if mid not in enabled:
                enabled.append(mid)
        ds = {
            "api_key_enc": doc["api_key_enc"],
            "key_hint": doc.get("key_hint"),
            "last_validated_at": doc.get("last_validated_at"),
            "configured_at": doc.get("configured_at"),
            "available_models": [],
            "enabled_models": enabled,
            "default_model": old_model,
            "models_synced_at": None,
        }
        providers["deepseek"] = ds
        doc["providers"] = providers
        model = old_model
        doc["slots"] = {
            sid: {"provider": "deepseek", "model": model} for sid in SLOT_IDS
        }
        for k in (
            "api_key_enc",
            "key_hint",
            "model",
            "base_url",
            "provider",
            "last_validated_at",
            "configured_at",
        ):
            doc.pop(k, None)
        return True
    return False


def ensure_migrated(user_id: str) -> dict:
    coll = get_db().user_llm_settings
    doc = coll.find_one({"user_id": user_id}, {"_id": 0}) or {}
    if not doc:
        return {}
    if _migrate_legacy_inplace(doc):
        unset_keys = {
            "api_key_enc": "",
            "key_hint": "",
            "model": "",
            "base_url": "",
            "provider": "",
            "last_validated_at": "",
            "configured_at": "",
        }
        coll.update_one(
            {"user_id": user_id},
            {
                "$set": {
                    "providers": doc["providers"],
                    "slots": doc["slots"],
                    "updated_at": _now(),
                    "user_id": user_id,
                },
                "$unset": unset_keys,
            },
            upsert=True,
        )
    return doc


def public_llm_settings(user_id: str) -> dict:
    doc = ensure_migrated(user_id)
    web = _web_public_fields(doc)
    providers_doc = doc.get("providers") or {}
    slots_doc = doc.get("slots") or {}
    configured = _any_provider_configured(doc)
    slots = {}
    for sid in SLOT_IDS:
        raw = slots_doc.get(sid)
        if not configured or not isinstance(raw, dict) or not raw.get("provider"):
            slots[sid] = None
        else:
            slots[sid] = {
                "provider": raw["provider"],
                "model": raw.get("model"),
            }
    return {
        "configured": configured,
        "providers": {
            pid: _provider_public(pid, providers_doc.get(pid)) for pid in PROVIDER_IDS
        },
        "slots": slots,
        **web,
    }
```

`get_llm_settings` 可继续返回原始文档；调用方逐步改走 `ensure_migrated`。

- [ ] **Step 4: 跑测确认通过**

Run: `cd backend && python -m pytest tests/test_llm_web_settings.py::test_public_defaults_without_doc tests/test_llm_web_settings.py::test_migrates_legacy_top_level_key -v`  
Expected: PASS

- [ ] **Step 5: Commit（默认跳过）**

```bash
git add backend/app/advisor/llm_settings.py backend/tests/test_llm_web_settings.py
git commit -m "feat: migrate LLM settings to providers and slots"
```

---

### Task 3: 保存 / 刷新 / 删除提供方

**Files:**
- Modify: `backend/app/advisor/llm_settings.py`
- Modify: `backend/tests/test_llm_web_settings.py`

**Interfaces:**
- Consumes: `list_model_ids`, `ping_chat`, `default_enabled_models`, `compute_default_model`, `intersect_enabled`
- Produces:
  - `save_provider_key(user_id: str, provider: str, api_key: str) -> dict`（公开设置）
  - `refresh_provider_models(user_id: str, provider: str) -> dict`
  - `clear_provider(user_id: str, provider: str) -> dict`
  - `clear_llm_settings(user_id: str) -> dict` 改为清除**全部**提供方 Key 与槽位，保留 Tavily

行为（与 spec 一致）：

- `save_provider_key`：先 `list_model_ids`（失败则 `ids=[]`, `list_ok=False`），ping 模型 = 系统默认（若在 ids）否则 ids[0] 否则系统默认；ping 失败则抛异常、不写库。list 成功则预勾；失败则 `enabled_models=[系统默认]`、`available_models=[]`。若用户此前没有任何 Key，八槽位全部填 `{provider, default_model}`。
- `refresh_provider_models`：无 Key 则 `ValueError`；list 失败则抛异常且不改快照；成功则求交，交空则 `default_enabled_models`，再空则前 3；重算 default；该提供方槽位模型不在 enabled 则改 default。
- `clear_provider`：删该提供方缓存字段；占用槽位改到剩余提供方按 `deepseek → kimi → qwen` 第一家；否则槽位全 `null`。
- 未知 `provider`：`ValueError("未知模型提供方")`

- [ ] **Step 1: 写失败单测**

```python
def _ok_list(ids):
    return lambda *a, **k: list(ids)


def _ok_ping(*a, **k):
    return None


def test_save_first_provider_fills_slots(db, monkeypatch):
    monkeypatch.setattr(llm_settings, "list_model_ids", _ok_list(["kimi-k2.6", "kimi-k3"]))
    monkeypatch.setattr(llm_settings, "ping_chat", _ok_ping)
    pub = llm_settings.save_provider_key("u1", "kimi", "sk-kimi-test-key")
    assert pub["configured"] is True
    assert pub["providers"]["kimi"]["configured"] is True
    assert pub["providers"]["kimi"]["enabled_models"] == ["kimi-k2.6"]
    assert pub["slots"]["agent"] == {"provider": "kimi", "model": "kimi-k2.6"}
    assert pub["slots"]["paper"]["provider"] == "kimi"


def test_save_second_provider_does_not_reset_slots(db, monkeypatch):
    monkeypatch.setattr(llm_settings, "list_model_ids", _ok_list(["deepseek-v4-flash"]))
    monkeypatch.setattr(llm_settings, "ping_chat", _ok_ping)
    llm_settings.save_provider_key("u1", "deepseek", "sk-ds-test-key")
    monkeypatch.setattr(llm_settings, "list_model_ids", _ok_list(["kimi-k2.6"]))
    pub = llm_settings.save_provider_key("u1", "kimi", "sk-kimi-test-key")
    assert pub["slots"]["agent"]["provider"] == "deepseek"
    assert pub["providers"]["kimi"]["configured"] is True


def test_ping_failure_does_not_save(db, monkeypatch):
    monkeypatch.setattr(llm_settings, "list_model_ids", _ok_list(["kimi-k2.6"]))
    def boom(*a, **k):
        raise RuntimeError("unauthorized")
    monkeypatch.setattr(llm_settings, "ping_chat", boom)
    with pytest.raises(RuntimeError):
        llm_settings.save_provider_key("u1", "kimi", "bad")
    assert llm_settings.public_llm_settings("u1")["configured"] is False


def test_list_failure_still_saves_key(db, monkeypatch):
    def boom_list(*a, **k):
        raise RuntimeError("models down")
    monkeypatch.setattr(llm_settings, "list_model_ids", boom_list)
    monkeypatch.setattr(llm_settings, "ping_chat", _ok_ping)
    pub = llm_settings.save_provider_key("u1", "deepseek", "sk-ds-test-key")
    assert pub["providers"]["deepseek"]["configured"] is True
    assert pub["providers"]["deepseek"]["available_models"] == []
    assert pub["providers"]["deepseek"]["enabled_models"] == ["deepseek-v4-flash"]
    assert pub["providers"]["deepseek"]["models_synced_at"] is None


def test_refresh_intersects_and_rewrites_slots(db, monkeypatch):
    monkeypatch.setattr(
        llm_settings, "list_model_ids", _ok_list(["deepseek-v4-flash", "deepseek-v4-pro"])
    )
    monkeypatch.setattr(llm_settings, "ping_chat", _ok_ping)
    llm_settings.save_provider_key("u1", "deepseek", "sk-ds-test-key")
    llm_settings.update_llm_settings(
        "u1",
        enabled_models={"deepseek": ["deepseek-v4-flash", "deepseek-v4-pro"]},
        slots={"agent": {"provider": "deepseek", "model": "deepseek-v4-pro"}},
    )
    monkeypatch.setattr(llm_settings, "list_model_ids", _ok_list(["deepseek-v4-flash"]))
    pub = llm_settings.refresh_provider_models("u1", "deepseek")
    assert pub["providers"]["deepseek"]["enabled_models"] == ["deepseek-v4-flash"]
    assert pub["slots"]["agent"]["model"] == "deepseek-v4-flash"


def test_clear_provider_remaps_to_remaining(db, monkeypatch):
    monkeypatch.setattr(llm_settings, "ping_chat", _ok_ping)
    monkeypatch.setattr(llm_settings, "list_model_ids", _ok_list(["deepseek-v4-flash"]))
    llm_settings.save_provider_key("u1", "deepseek", "sk-ds-test-key")
    monkeypatch.setattr(llm_settings, "list_model_ids", _ok_list(["kimi-k2.6"]))
    llm_settings.save_provider_key("u1", "kimi", "sk-kimi-test-key")
    pub = llm_settings.clear_provider("u1", "deepseek")
    assert pub["providers"]["deepseek"]["configured"] is False
    assert pub["slots"]["agent"]["provider"] == "kimi"
    assert pub["tavily_configured"] is False
```

注意：`test_refresh_intersects_and_rewrites_slots` 依赖 Task 4 的 `update_llm_settings(enabled_models=, slots=)`。若本任务先跑失败，把该测试挪到 Task 4；本任务 refresh 用例改为直接改 FakeColl 里 `enabled_models` / `slots` 再调 `refresh_provider_models`：

```python
    # after save_provider_key deepseek
    doc = db.user_llm_settings.docs["u1"]
    doc["providers"]["deepseek"]["enabled_models"] = [
        "deepseek-v4-flash",
        "deepseek-v4-pro",
    ]
    doc["slots"]["agent"]["model"] = "deepseek-v4-pro"
```

- [ ] **Step 2: 跑测确认失败**

Run: `cd backend && python -m pytest tests/test_llm_web_settings.py -k "save_first or save_second or ping_failure or list_failure or refresh_intersects or clear_provider_remaps" -v`  
Expected: FAIL（函数不存在）

- [ ] **Step 3: 实现 save / refresh / clear**

在 `llm_settings.py` 顶部：`from .llm_providers import list_model_ids, ping_chat, default_enabled_models, intersect_enabled`（测试里 `monkeypatch.setattr(llm_settings, "list_model_ids", ...)` 要求这两个名字在 `llm_settings` 模块命名空间；可 `from .llm_providers import list_model_ids as list_model_ids`）。

`save_provider_key` 核心：

```python
def save_provider_key(user_id: str, provider: str, api_key: str) -> dict:
    if provider not in PROVIDERS:
        raise ValueError("未知模型提供方")
    raw = api_key.strip()
    if not raw:
        raise ValueError("请填写 API Key")
    spec = PROVIDERS[provider]
    list_ok = True
    try:
        ids = list_model_ids(raw, spec["base_url"])
    except Exception:
        ids = []
        list_ok = False
    ping_model = spec["default_model"]
    if ids:
        ping_model = ping_model if ping_model in ids else ids[0]
    ping_chat(raw, spec["base_url"], ping_model, provider=provider)
    now = _now()
    if list_ok:
        available = [{"id": mid} for mid in ids]
        enabled = default_enabled_models(provider, ids)
        synced = now
    else:
        available = []
        enabled = [spec["default_model"]]
        synced = None
    default_model = compute_default_model(provider, enabled)
    existing = ensure_migrated(user_id)
    was_any = _any_provider_configured(existing)
    providers = dict(existing.get("providers") or {})
    prev = dict(providers.get(provider) or {})
    providers[provider] = {
        **prev,
        "api_key_enc": encrypt_api_key(raw),
        "key_hint": key_hint(raw),
        "last_validated_at": now,
        "configured_at": prev.get("configured_at") or now,
        "available_models": available,
        "enabled_models": enabled,
        "default_model": default_model,
        "models_synced_at": synced,
    }
    slots = dict(existing.get("slots") or {})
    if not was_any:
        slots = {sid: {"provider": provider, "model": default_model} for sid in SLOT_IDS}
    get_db().user_llm_settings.update_one(
        {"user_id": user_id},
        {
            "$set": {
                "user_id": user_id,
                "providers": providers,
                "slots": slots,
                "updated_at": now,
            },
            "$setOnInsert": {"created_at": now},
        },
        upsert=True,
    )
    return public_llm_settings(user_id)
```

`refresh_provider_models`：解密已存 Key → `list_model_ids`（失败则原样 raise）→ 求交/回退 → 修正槽位。

`clear_provider`：`providers.pop` 或置空该 pid；`_remap_slots_after_clear(slots, providers)`：

```python
def _first_remaining_provider(providers: dict) -> str | None:
    for pid in PROVIDER_IDS:
        if (providers.get(pid) or {}).get("api_key_enc"):
            return pid
    return None


def _remap_slots(slots: dict, providers: dict) -> dict:
    remain = _first_remaining_provider(providers)
    if remain is None:
        return {sid: None for sid in SLOT_IDS}
    default = (providers[remain] or {}).get("default_model") or PROVIDERS[remain]["default_model"]
    out = {}
    for sid in SLOT_IDS:
        cur = slots.get(sid) if isinstance(slots.get(sid), dict) else None
        if cur and (providers.get(cur.get("provider")) or {}).get("api_key_enc"):
            pdata = providers[cur["provider"]]
            enabled = list(pdata.get("enabled_models") or [])
            model = cur.get("model") if cur.get("model") in enabled else pdata.get("default_model")
            out[sid] = {"provider": cur["provider"], "model": model}
        else:
            out[sid] = {"provider": remain, "model": default}
    return out
```

`clear_llm_settings`：对每个 pid 去掉 Key 字段，slots 全 None，**不要** unset Tavily。

把旧 `validate_deepseek_key` 改为调用 `ping_chat(..., provider="deepseek")`，避免残留第二套校验。

- [ ] **Step 4: 跑测确认通过**

Run: `cd backend && python -m pytest tests/test_llm_web_settings.py -v`  
Expected: 到这一步，原 `update_llm_settings(api_key=)` / `test_web_tool_flags` / `test_clear_deepseek_keeps_tavily` 可能仍 FAIL——下一任务修兼容层。本任务新用例须 PASS。可先 `pytest -k "save_first or save_second or ping_failure or list_failure or refresh_intersects or clear_provider_remaps or migrates or public_defaults or tavily"`。

- [ ] **Step 5: Commit（默认跳过）**

```bash
git add backend/app/advisor/llm_settings.py backend/tests/test_llm_web_settings.py
git commit -m "feat: save, refresh, and clear per LLM provider"
```

---

### Task 4: 槽位 / 勾选保存，以及旧 PUT 兼容

**Files:**
- Modify: `backend/app/advisor/llm_settings.py` 的 `update_llm_settings`
- Modify: `backend/tests/test_llm_web_settings.py`（修好旧 `api_key=` 用例）

**Interfaces:**
- Consumes: Task 3 的 `save_provider_key`
- Produces: `update_llm_settings(..., enabled_models: dict[str, list[str]] | None = None, slots: dict[str, dict] | None = None, api_key=..., model=...)`
  - `enabled_models[pid]`：提供方必须已配置；非空；若 `available_models` 非空则必须是其 id 子集；若 available 为空则只允许系统默认 id。然后重算 `default_model`，该提供方槽位模型不在新勾选则改为 default。
  - `slots[sid]`：`sid` 必须在 `SLOT_IDS`；`provider` 已配置；`model` 在（合并后的）该提供方 `enabled_models`。未出现在 body 的槽位保持原值。
  - 旧 `api_key`：调用 `save_provider_key(user_id, "deepseek", api_key)`（仍校验+拉模型）。
  - 旧 `model` 且 `slots is None`：所有 `provider==deepseek` 的槽位改为该 model（须在 DeepSeek enabled 中，否则 400）。DeepSeek 未配置则 `ValueError("请先在模型配置中填写 API Key")`。

- [ ] **Step 1: 写失败单测并修旧用例**

```python
def test_update_slots_rejects_unconfigured_provider(db, monkeypatch):
    monkeypatch.setattr(llm_settings, "list_model_ids", _ok_list(["deepseek-v4-flash"]))
    monkeypatch.setattr(llm_settings, "ping_chat", _ok_ping)
    llm_settings.save_provider_key("u1", "deepseek", "sk-ds-test-key")
    with pytest.raises(ValueError, match="未配置"):
        llm_settings.update_llm_settings(
            "u1",
            slots={"agent": {"provider": "kimi", "model": "kimi-k2.6"}},
        )


def test_update_enabled_and_slot_ok(db, monkeypatch):
    monkeypatch.setattr(
        llm_settings,
        "list_model_ids",
        _ok_list(["deepseek-v4-flash", "deepseek-v4-pro"]),
    )
    monkeypatch.setattr(llm_settings, "ping_chat", _ok_ping)
    llm_settings.save_provider_key("u1", "deepseek", "sk-ds-test-key")
    pub = llm_settings.update_llm_settings(
        "u1",
        enabled_models={"deepseek": ["deepseek-v4-pro"]},
        slots={"agent": {"provider": "deepseek", "model": "deepseek-v4-pro"}},
    )
    assert pub["providers"]["deepseek"]["default_model"] == "deepseek-v4-pro"
    assert pub["slots"]["paper"]["model"] == "deepseek-v4-pro"  # remapped off flash if it was flash-only after uncheck


def test_legacy_api_key_writes_deepseek(db, monkeypatch):
    monkeypatch.setattr(llm_settings, "list_model_ids", _ok_list(["deepseek-v4-flash"]))
    monkeypatch.setattr(llm_settings, "ping_chat", _ok_ping)
    pub = llm_settings.update_llm_settings("u1", api_key="sk-deepseek-test-key")
    assert pub["providers"]["deepseek"]["configured"] is True
```

把原 `test_clear_deepseek_keeps_tavily` / `test_web_tool_flags` 里对 `validate_deepseek_key` 的 monkeypatch 改成 `list_model_ids` + `ping_chat`（`web_tool_flags` 断言放到 Task 5 再改期望）。本任务先让 `update_llm_settings(api_key=)` 不再依赖 `validate_deepseek_key`。

`test_update_enabled_and_slot_ok`：取消 flash 只留 pro 后，所有 DeepSeek 槽位（含 paper）应变为 pro。

- [ ] **Step 2: 跑测确认失败**

Run: `cd backend && python -m pytest tests/test_llm_web_settings.py::test_update_slots_rejects_unconfigured_provider tests/test_llm_web_settings.py::test_update_enabled_and_slot_ok tests/test_llm_web_settings.py::test_legacy_api_key_writes_deepseek -v`  
Expected: FAIL

- [ ] **Step 3: 实现 `update_llm_settings` 新字段**

签名增加 `enabled_models` / `slots`。处理顺序：1) 若 `api_key` 非空 → `save_provider_key(..., "deepseek", api_key)`；2) 再读 `ensure_migrated`；3) 应用 `enabled_models`；4) 应用 `slots`；5) 旧 `model`；6) Tavily/web 开关（现逻辑）。`has_change` 把新字段算进去。

校验文案用中文：`未知槽位`、`请先配置该模型提供方`、`模型未在可用列表中勾选`、`至少勾选一个模型`。

- [ ] **Step 4: 跑测确认通过**

Run: `cd backend && python -m pytest tests/test_llm_web_settings.py -v`  
Expected: `test_web_tool_flags` 可能仍按旧顶层 Key 语义部分失败，其余 PASS。

- [ ] **Step 5: Commit（默认跳过）**

```bash
git add backend/app/advisor/llm_settings.py backend/tests/test_llm_web_settings.py
git commit -m "feat: save per-slot LLM provider and enabled models"
```

---

### Task 5: resolve 凭证与 web_research 挂载条件

**Files:**
- Modify: `backend/app/advisor/llm_settings.py`（`resolve_llm_credentials`, `web_tool_flags`, 新增 `resolve_deepseek_api_key`）
- Modify: `backend/tests/test_llm_web_settings.py`

**Interfaces:**
- Produces:
  - `resolve_llm_credentials(user_id: str, slot: str) -> dict[str, str]` 返回 `api_key`, `model`, `base_url`, `provider`。缺槽位/缺 Key 抛 `ValueError(MISSING_KEY_MESSAGE)`。未知 slot 抛 `ValueError("未知模型槽位")`。运行时**不**因未勾选而改模型名。
  - `resolve_deepseek_api_key(user_id: str) -> str | None`
  - `web_tool_flags(user_id: str, *, agent_tools: bool = True) -> dict[str, bool]`
    - `tavily`：开关 ∧ Tavily Key（不变）
    - `web_research`（`agent_tools=True`）：开关 ∧ DeepSeek Key ∧ `slots.agent.provider == "deepseek"`
    - `web_research`（`agent_tools=False`）：开关 ∧ DeepSeek Key

- [ ] **Step 1: 写失败单测**

```python
def test_resolve_slot_and_missing(db, monkeypatch):
    monkeypatch.setattr(llm_settings, "list_model_ids", _ok_list(["kimi-k2.6"]))
    monkeypatch.setattr(llm_settings, "ping_chat", _ok_ping)
    with pytest.raises(ValueError, match="模型配置"):
        llm_settings.resolve_llm_credentials("u1", "agent")
    llm_settings.save_provider_key("u1", "kimi", "sk-kimi-test-key")
    creds = llm_settings.resolve_llm_credentials("u1", "agent")
    assert creds["provider"] == "kimi"
    assert creds["model"] == "kimi-k2.6"
    assert creds["base_url"] == "https://api.moonshot.cn/v1"
    assert creds["api_key"].startswith("sk-kimi")


def test_web_tool_flags_agent_vs_home(db, monkeypatch):
    monkeypatch.setattr(llm_settings, "ping_chat", _ok_ping)
    monkeypatch.setattr(llm_settings, "list_model_ids", _ok_list(["deepseek-v4-flash"]))
    llm_settings.save_provider_key("u1", "deepseek", "sk-ds-test-key")
    monkeypatch.setattr(llm_settings, "list_model_ids", _ok_list(["kimi-k2.6"]))
    llm_settings.save_provider_key("u1", "kimi", "sk-kimi-test-key")
    llm_settings.update_llm_settings(
        "u1", slots={"agent": {"provider": "kimi", "model": "kimi-k2.6"}}
    )
    agent_flags = llm_settings.web_tool_flags("u1", agent_tools=True)
    home_flags = llm_settings.web_tool_flags("u1", agent_tools=False)
    assert agent_flags["web_research"] is False
    assert home_flags["web_research"] is True
    assert llm_settings.resolve_deepseek_api_key("u1",).startswith("sk-ds")
```

并把原 `test_web_tool_flags` 改成：只配 DeepSeek 时 `web_tool_flags("u1")["web_research"] is True`（agent 槽位默认 DeepSeek）。

- [ ] **Step 2: 跑测确认失败**

Run: `cd backend && python -m pytest tests/test_llm_web_settings.py::test_resolve_slot_and_missing tests/test_llm_web_settings.py::test_web_tool_flags_agent_vs_home -v`  
Expected: FAIL（`resolve` 仍是单参数）

- [ ] **Step 3: 改签名并实现**

```python
def resolve_llm_credentials(user_id: str, slot: str) -> dict[str, str]:
    if slot not in SLOT_IDS:
        raise ValueError("未知模型槽位")
    doc = ensure_migrated(user_id)
    raw = (doc.get("slots") or {}).get(slot)
    if not isinstance(raw, dict) or not raw.get("provider"):
        raise ValueError(MISSING_KEY_MESSAGE)
    pid = str(raw["provider"])
    pdata = (doc.get("providers") or {}).get(pid) or {}
    if not pdata.get("api_key_enc"):
        raise ValueError(MISSING_KEY_MESSAGE)
    return {
        "api_key": decrypt_api_key(pdata["api_key_enc"]),
        "model": str(raw.get("model") or pdata.get("default_model") or PROVIDERS[pid]["default_model"]),
        "base_url": str(PROVIDERS[pid]["base_url"]).rstrip("/"),
        "provider": pid,
    }


def resolve_deepseek_api_key(user_id: str) -> str | None:
    doc = ensure_migrated(user_id)
    enc = ((doc.get("providers") or {}).get("deepseek") or {}).get("api_key_enc")
    if not enc:
        return None
    try:
        return decrypt_api_key(enc)
    except Exception:
        return None


def web_tool_flags(user_id: str, *, agent_tools: bool = True) -> dict[str, bool]:
    doc = ensure_migrated(user_id) or {}
    # web_research_enabled / tavily 默认值逻辑与现网相同
    ...
    has_ds = bool(((doc.get("providers") or {}).get("deepseek") or {}).get("api_key_enc"))
    agent_is_ds = False
    slot = (doc.get("slots") or {}).get("agent")
    if isinstance(slot, dict) and slot.get("provider") == "deepseek":
        agent_is_ds = True
    research = bool(web_research_enabled) and has_ds and (agent_is_ds if agent_tools else True)
    return {"web_research": research, "tavily": bool(tavily_enabled) and bool(doc.get("tavily_api_key_enc"))}
```

- [ ] **Step 4: 跑测确认通过**

Run: `cd backend && python -m pytest tests/test_llm_web_settings.py tests/test_llm_providers.py -v`  
Expected: PASS

- [ ] **Step 5: Commit（默认跳过）**

```bash
git add backend/app/advisor/llm_settings.py backend/tests/test_llm_web_settings.py
git commit -m "feat: resolve LLM credentials by slot"
```

---

### Task 6: `build_chat_model` 按槽位构建，Kimi 不传 temperature

**Files:**
- Modify: `backend/app/advisor/agent/llm.py`
- Modify: `backend/tests/test_committee_llm.py`

**Interfaces:**
- Consumes: `resolve_llm_credentials(user_id, slot) -> {api_key, model, base_url, provider}`
- Produces: `build_chat_model(user_id, *, slot: str | None = None, tier: Literal["quick","deep"] | None = None, temperature=0.3, streaming=True, request_timeout=None, committee_config=None)`
  - `tier` 与 `slot` 必须至少有一个；`tier` 优先：`quick→committee_quick`，`deep→committee_deep`
  - 不再用 `committee_config["models"]` 覆盖模型名；`committee_config` 参数可留着以免调用方报 unexpected kwarg，但忽略其中 models
  - `provider=="kimi"`：kwargs **不含** `temperature` / `top_p` / `presence_penalty` / `frequency_penalty`

- [ ] **Step 1: 改写失败单测**

替换 `test_build_chat_model_preserves_default_and_supports_tiers` 与 `test_tier_loads_committee_defaults_and_request_timeout`：

```python
def test_build_chat_model_uses_slot_and_tier(monkeypatch):
    def fake_resolve(user_id, slot):
        models = {
            "agent": "user-default",
            "committee_quick": "fast",
            "committee_deep": "reasoner",
        }
        return {
            "api_key": "secret",
            "base_url": "https://llm.example/v1",
            "model": models[slot],
            "provider": "deepseek",
        }

    monkeypatch.setattr(llm, "resolve_llm_credentials", fake_resolve)
    monkeypatch.setattr(llm, "ChatOpenAI", FakeChatModel)

    default = llm.build_chat_model("u", slot="agent")
    quick = llm.build_chat_model("u", tier="quick")
    deep = llm.build_chat_model("u", tier="deep", request_timeout=12)
    assert default.kwargs["model"] == "user-default"
    assert quick.kwargs["model"] == "fast"
    assert deep.kwargs["model"] == "reasoner"
    assert deep.kwargs["timeout"] == 12
    assert "temperature" in default.kwargs


def test_kimi_omits_temperature(monkeypatch):
    monkeypatch.setattr(
        llm,
        "resolve_llm_credentials",
        lambda user_id, slot: {
            "api_key": "secret",
            "base_url": "https://api.moonshot.cn/v1",
            "model": "kimi-k2.6",
            "provider": "kimi",
        },
    )
    monkeypatch.setattr(llm, "ChatOpenAI", FakeChatModel)
    model = llm.build_chat_model("u", slot="paper", temperature=0.2)
    assert "temperature" not in model.kwargs
    assert model.kwargs["model"] == "kimi-k2.6"


def test_build_chat_model_requires_slot_or_tier(monkeypatch):
    monkeypatch.setattr(llm, "ChatOpenAI", FakeChatModel)
    try:
        llm.build_chat_model("u")
        raise AssertionError("expected TypeError or ValueError")
    except (TypeError, ValueError):
        pass
```

保留 `test_yaml_contains_committee_models_and_budget_defaults`（yaml 可暂留未读字段）与 `test_legacy_agent_chat_public_signatures_remain_compatible`。

- [ ] **Step 2: 跑测确认失败**

Run: `cd backend && python -m pytest tests/test_committee_llm.py -v`  
Expected: FAIL（旧签名仍默认无 slot）

- [ ] **Step 3: 重写 `llm.py`**

```python
"""Build ChatOpenAI client for a user's per-slot LLM credentials."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from langchain_openai import ChatOpenAI

from ..llm_settings import resolve_llm_credentials

_TIER_SLOT = {"quick": "committee_quick", "deep": "committee_deep"}


def build_chat_model(
    user_id: str,
    *,
    slot: str | None = None,
    temperature: float = 0.3,
    streaming: bool = True,
    tier: Literal["quick", "deep"] | None = None,
    committee_config: Mapping[str, Any] | None = None,  # unused; kept for callers
    request_timeout: float | None = None,
) -> ChatOpenAI:
    if tier is not None:
        slot = _TIER_SLOT[tier]
    if not slot:
        raise ValueError("build_chat_model 需要 slot 或 tier")
    creds = resolve_llm_credentials(user_id, slot)
    kwargs: dict[str, Any] = {
        "api_key": creds["api_key"],
        "base_url": creds["base_url"],
        "model": creds["model"],
        "streaming": streaming,
        "stream_usage": streaming,
    }
    if creds.get("provider") != "kimi":
        kwargs["temperature"] = temperature
    if request_timeout is not None:
        kwargs["timeout"] = request_timeout
    return ChatOpenAI(**kwargs)
```

- [ ] **Step 4: 跑测确认通过**

Run: `cd backend && python -m pytest tests/test_committee_llm.py -v`  
Expected: PASS

- [ ] **Step 5: Commit（默认跳过）**

```bash
git add backend/app/advisor/agent/llm.py backend/tests/test_committee_llm.py
git commit -m "feat: build chat model from slot and omit Kimi temperature"
```

---

### Task 7: REST 路由与 403 文案

**Files:**
- Modify: `backend/app/advisor/routes.py`
  - `LlmSettingsBody` 增加 `enabled_models: dict[str, list[str]] | None`、`slots: dict[str, dict] | None`
  - 新增 `LlmProviderKeyBody`：`api_key: str`
  - `PUT /llm/providers/{provider_id}` → `save_provider_key`；校验失败 502，`detail=f"{PROVIDERS[id]['label']} 校验失败: {type(exc).__name__}"`；未知 id → 404
  - `POST /llm/providers/{provider_id}/models/refresh` → `refresh_provider_models`；无 Key → 400；拉列表失败 → 502
  - `DELETE /llm/providers/{provider_id}` → `clear_provider`
  - 现有 `PUT /llm/settings` 传入新字段；`api_key` 兼容仍走 `update_llm_settings`
  - `DELETE /llm/settings` 仍调 `clear_llm_settings`（现为清全部提供方）
  - 所有 `detail="请先配置 DeepSeek API Key"` 改为 `HTTP_MISSING_KEY_DETAIL`；判断条件改为 `"API Key" in detail or "模型配置" in detail`（去掉只认 DeepSeek）
- 从 `llm_settings` import `HTTP_MISSING_KEY_DETAIL`, `save_provider_key`, `refresh_provider_models`, `clear_provider`, `PROVIDERS`（或只 import 函数）

不单独加 HTTP 测试；服务层已覆盖。本任务改完后跑一遍会受影响的 403 单测并改 match 字符串。

**连带测试文案：**

- `backend/tests/test_limitup_promote.py`：`match="DeepSeek"` → `match="API Key"`
- `backend/tests/test_limitup_promote_store.py`：mock 的 ValueError 与 `match` 改为新文案
- `backend/tests/test_monitor_store.py`：`create_job` 包装文案改为 `请先在模型配置中填写 API Key`，`match="API Key"` 或 `match="模型配置"`

- [ ] **Step 1: 改 `monitor/store.py` 包装错误**

```python
        try:
            resolve_llm_credentials(user_id, "monitor")
        except ValueError as exc:
            raise ValueError("请先在模型配置中填写 API Key") from exc
```

其它 `resolve_llm_credentials(uid)` 暂留到 Task 8 一并加 slot；本任务若先跑 monitor 测试，至少把抛给用户的字符串改掉。为避免 `resolve` 双参数立刻炸，本任务**先改 routes 文案与 Body**，`resolve` 调用的 slot 放到 Task 8。若 Task 5 已把 `resolve` 改为必填 slot，monitor 测试会在 Task 8 前失败——因此 **Task 7 与 Task 8 必须连续做完再宣称后端绿**。

- [ ] **Step 2: 实现路由**

```python
class LlmProviderKeyBody(BaseModel):
    api_key: str


class LlmSettingsBody(BaseModel):
    api_key: str | None = Field(default=None)
    model: str | None = Field(default=None)
    base_url: str | None = Field(default=None)
    web_research_enabled: bool | None = Field(default=None)
    tavily_enabled: bool | None = Field(default=None)
    tavily_api_key: str | None = Field(default=None)
    enabled_models: dict[str, list[str]] | None = Field(default=None)
    slots: dict[str, dict[str, str]] | None = Field(default=None)


@router.put("/llm/providers/{provider_id}")
def llm_provider_put(provider_id: str, body: LlmProviderKeyBody, user=Depends(_user)):
    uid = _bind(user)
    if provider_id not in ("deepseek", "kimi", "qwen"):
        raise HTTPException(status_code=404, detail="未知模型提供方")
    try:
        return save_provider_key(uid, provider_id, body.api_key)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        label = {"deepseek": "DeepSeek", "kimi": "Kimi", "qwen": "千问"}[provider_id]
        raise HTTPException(
            status_code=502,
            detail=f"{label} 校验失败: {type(exc).__name__}",
        ) from exc
```

对 `update_llm_settings` 传入 `enabled_models=body.enabled_models, slots=body.slots`。

把 routes 里所有 403 DeepSeek 字符串换成 `HTTP_MISSING_KEY_DETAIL`。

- [ ] **Step 3: 跑现有会匹配 DeepSeek 的测试并改断言**

Run: `cd backend && python -m pytest tests/test_limitup_promote.py tests/test_limitup_promote_store.py tests/test_monitor_store.py -v`  
Expected: 改 match 后，若 `resolve_llm_credentials` 缺 slot 仍可能 FAIL → Task 8。

- [ ] **Step 4: Commit（默认跳过）**

```bash
git add backend/app/advisor/routes.py backend/app/advisor/monitor/store.py backend/tests/test_limitup_promote.py backend/tests/test_limitup_promote_store.py backend/tests/test_monitor_store.py
git commit -m "feat: add per-provider LLM settings routes"
```

---

### Task 8: 所有 LLM 调用点传入 slot，web_research 改读 DeepSeek Key

**Files:**
- Modify: `backend/app/advisor/agent/graph.py` — `build_chat_model(user_id, slot="agent")`；`SYSTEM_PROMPT` 去掉「（DeepSeek）」；规则 24「须已配置 DeepSeek」→「须已配置 API Key」；`_runtime_config_section` 用 `slots.agent` 与 `providers[pid]`
- Modify: `backend/app/advisor/agent/data_agent/graph.py` — `slot="agent"`
- Modify: `backend/app/advisor/paper_trader/decide.py` — `resolve_llm_credentials(user_id, "paper")`；`build_chat_model(..., slot="paper")`
- Modify: `backend/app/advisor/home_news_brief.py` — `slot="home"`；`_maybe_fetch_web_items` 用 `web_tool_flags(user_id, agent_tools=False)` + `resolve_deepseek_api_key`
- Modify: `backend/app/advisor/home_news_stock_picks.py` — `slot="home"`
- Modify: `backend/app/advisor/agent/recommendations.py` — `slot="home"`
- Modify: `backend/app/advisor/monitor/llm_watch.py` — `slot="monitor"`
- Modify: `backend/app/advisor/monitor/store.py` — `resolve_llm_credentials(user_id, "monitor")`（若 Task 7 未改参数）
- Modify: `backend/app/advisor/policy_watch/interpret.py` — `slot="policy"`
- Modify: `backend/app/advisor/limitup_promote.py` — `resolve`/`build` 用 `slot="limitup"`
- Modify: `backend/app/advisor/limitup_promote_store.py` — `resolve_llm_credentials(user_id, "limitup")`
- Modify: `backend/app/advisor/committee/agents.py` — 已有 `tier=`，保持即可（Task 6 已映射槽位）
- Modify: `backend/app/advisor/agent/web_tools.py` — `resolve_deepseek_api_key` 替代 `resolve_llm_credentials`；无 Key 返回 `错误：未配置 DeepSeek API Key`（工具名仍是 DeepSeek 联网综述）
- Modify: `backend/app/advisor/routes.py` 中所有 `resolve_llm_credentials(uid)` 按接口选 slot：晋级流 `limitup`，主对话相关 `agent`（约 L307 及 chat/home 刷新处）
- Modify: `backend/app/advisor/agent/tools.py` `get_user_data_overview`：JSON 里 `model` / `provider` 取 `(llm.get("slots") or {}).get("agent")`，不再读顶层 `llm["model"]`
- Modify: 所有 `monkeypatch.setattr(..., "resolve_llm_credentials", lambda uid: ...)` 改为 `lambda uid, slot=None, **k: ...` 或 `lambda *a, **k:`
  搜索：`backend/tests` 下 `resolve_llm_credentials`

`home_news_brief._maybe_fetch_web_items`：

```python
    flags = web_tool_flags(user_id, agent_tools=False)
    if not flags.get("web_research"):
        return []
    key = resolve_deepseek_api_key(user_id)
    if not key:
        return []
    raw = run_web_research(key, "今日A股市场政策与舆情热点摘要（简体中文，列要点）")
```

`_runtime_config_section`：

```python
    llm = public_llm_settings(user_id)
    agent = (llm.get("slots") or {}).get("agent") or {}
    pid = agent.get("provider") or "（未配置）"
    model = agent.get("model") or "（未配置）"
    configured = "已配置" if llm.get("configured") else "未配置"
    return (
        "## 运行配置\n"
        f"- 模型：{configured}\n"
        f"- 主对话提供方：{pid}\n"
        f"- 主对话模型：{model}\n"
        ...
    )
```

- [ ] **Step 1: 全局替换调用点（无单独新测，用现有测当回归）**

每个 `build_chat_model(user_id` 补 `slot=`（委员会除外）。每个裸 `resolve_llm_credentials(user_id)` 补 slot。

- [ ] **Step 2: 修测试 mock 签名**

Run: `cd backend && rg -n "resolve_llm_credentials" tests`  
所有 `lambda uid:` mock 改为接受第二参数。`test_home_news_brief.py` 等 `lambda uid: {"api_key": "x"}` 改为 `lambda uid, slot=None: {"api_key": "x"}`。

- [ ] **Step 3: 跑后端相关测试**

Run:

```bash
cd backend && python -m pytest tests/test_llm_web_settings.py tests/test_llm_providers.py tests/test_committee_llm.py tests/test_web_tools_mount.py tests/test_home_news_brief.py tests/test_limitup_promote.py tests/test_limitup_promote_store.py tests/test_monitor_store.py tests/test_monitor_llm_watch.py tests/test_paper_trader_decide.py tests/test_policy_watch_interpret.py tests/test_agent_ephemeral_run.py -q
```

Expected: PASS（若某文件无测试则从命令去掉）。失败则按缺 `slot=` 的 traceback 补全。

再扫一遍：

```bash
cd backend && python -m pytest tests -q --tb=no
```

Expected: 与本功能相关的失败必须清零。

- [ ] **Step 4: Commit（默认跳过）**

```bash
git add backend
git commit -m "feat: pass LLM slot through all chat call sites"
```

---

### Task 9: 设置页 UI 与 agentApi

**Files:**
- Modify: `frontend-advisor/src/agentApi.ts`
- Modify: `frontend-advisor/src/pages/AgentSettingsPage.tsx`
- Modify: `frontend-advisor/src/pages/AgentSettingsPage.test.tsx`

**Interfaces:**
- Produces 前端类型：

```ts
export type LlmProviderId = 'deepseek' | 'kimi' | 'qwen'
export type LlmSlotId =
  | 'agent'
  | 'paper'
  | 'home'
  | 'monitor'
  | 'policy'
  | 'limitup'
  | 'committee_quick'
  | 'committee_deep'

export type LlmProviderPublic = {
  configured: boolean
  key_hint?: string | null
  last_validated_at?: string | null
  available_models: { id: string }[]
  enabled_models: string[]
  default_model: string
  models_synced_at?: string | null
}

export type LlmSettings = {
  configured: boolean
  providers: Record<LlmProviderId, LlmProviderPublic>
  slots: Record<LlmSlotId, { provider: LlmProviderId; model: string } | null>
  web_research_enabled?: boolean
  tavily_enabled?: boolean
  tavily_configured?: boolean
  tavily_key_hint?: string | null
  tavily_validated_at?: string | null
}
```

API 函数：`saveLlmProvider(id, apiKey)` PUT `/api/advisor/llm/providers/${id}`；`refreshLlmProviderModels(id)` POST `.../models/refresh`；`clearLlmProvider(id)` DELETE 该路径；`saveLlmSettings` body 改为 `{ enabled_models?, slots?, web_research_enabled?, tavily_enabled?, tavily_api_key? }`；保留 `clearLlmSettings` DELETE `/settings`。

页面结构：

1. hero：配置 DeepSeek / Kimi / 千问；Key 服务端加密。
2. 三张卡片，标题 DeepSeek / Kimi / 千问，文档链接用 `docs_url`（可写死与 spec 相同）。每张：Key 输入、保存（只提交该提供方）、已配置则清除、模型复选框、刷新模型。保存/刷新/清除独立 loading。
3. 「功能模块」八行，常量：

```ts
const SLOT_ROWS: { id: LlmSlotId; label: string }[] = [
  { id: 'agent', label: '主 Agent 对话' },
  { id: 'paper', label: '模拟盘' },
  { id: 'home', label: '首页解读' },
  { id: 'monitor', label: '定时任务' },
  { id: 'policy', label: '政策雷达' },
  { id: 'limitup', label: '打板晋级' },
  { id: 'committee_quick', label: '委员会·快速' },
  { id: 'committee_deep', label: '委员会·深度' },
]
```

提供方 `<select>` 选项 = `settings.providers[id].configured`。切换提供方时 `model = providers[next].default_model`。模型 `<select>` 选项 = `enabled_models`。`configured===false` 时整块 disabled + 提示「请先配置至少一个模型提供方」。模块区「保存」走 `saveLlmSettings({ enabled_models, slots, web... })`。勾选变化可先留在本地 state，与模块一起保存；刷新模型后用服务端返回覆盖。

4. 联网搜索：主 Agent 槽位 provider !== `deepseek` 时综述 checkbox `disabled`，旁注「仅主 Agent 使用 DeepSeek 时可用」。Tavily 区块保持现逻辑。

- [ ] **Step 1: 写失败单测（新交互）**

在 `AgentSettingsPage.test.tsx` 增加完整 `LlmSettings` mock（三家 providers + slots）。新用例：

```tsx
function configuredFixture(over: Partial<api.LlmSettings> = {}): api.LlmSettings {
  const deepseek = {
    configured: true,
    key_hint: 'sk-t…est1',
    available_models: [{ id: 'deepseek-v4-flash' }, { id: 'deepseek-v4-pro' }],
    enabled_models: ['deepseek-v4-flash', 'deepseek-v4-pro'],
    default_model: 'deepseek-v4-flash',
    last_validated_at: null,
    models_synced_at: '2026-08-17T00:00:00Z',
  }
  const empty = {
    configured: false,
    key_hint: null,
    available_models: [] as { id: string }[],
    enabled_models: [] as string[],
    default_model: 'kimi-k2.6',
    last_validated_at: null,
    models_synced_at: null,
  }
  const slot = { provider: 'deepseek' as const, model: 'deepseek-v4-flash' }
  return {
    configured: true,
    providers: {
      deepseek,
      kimi: { ...empty, default_model: 'kimi-k2.6' },
      qwen: { ...empty, default_model: 'qwen3.7-plus' },
    },
    slots: {
      agent: slot,
      paper: slot,
      home: slot,
      monitor: slot,
      policy: slot,
      limitup: slot,
      committee_quick: slot,
      committee_deep: slot,
    },
    web_research_enabled: true,
    tavily_enabled: false,
    tavily_configured: false,
    ...over,
  }
}
```

- `shows provider cards and slot rows`：能看到「Kimi」「千问」「主 Agent 对话」
- `hides unconfigured provider from slot dropdown`：主 Agent 提供方 select 没有 `kimi` option（用 `getByLabelText` 或该行 combobox）
- `switching provider sets default model`：mock kimi configured + enabled `kimi-k2.6`；把 agent 提供方改成 Kimi 后，模型值为 `kimi-k2.6`
- `disables web research when agent is not deepseek`：fixture 里 agent.provider=`kimi`（且 kimi configured），综述 checkbox disabled
- 保留原 Tavily 三则，mock 改为 `configuredFixture()`

`vi.mock` 增加 `saveLlmProvider` / `refreshLlmProviderModels` / `clearLlmProvider`。

- [ ] **Step 2: 跑测确认失败**

Run: `cd frontend-advisor && npm test -- src/pages/AgentSettingsPage.test.tsx`  
Expected: FAIL（页面仍是旧 DeepSeek 表单）

- [ ] **Step 3: 实现 API 类型与页面**

按上面结构重写 `AgentSettingsPage`。本地 `enabled` 与 `slots` 从 GET 初始化。提供方 Key 保存成功后 `setSettings`。复选框至少保留 1 个（取消最后一个时忽略或 toast「至少勾选一个模型」）。

- [ ] **Step 4: 跑测确认通过**

Run: `cd frontend-advisor && npm test -- src/pages/AgentSettingsPage.test.tsx`  
Expected: PASS

- [ ] **Step 5: Commit（默认跳过）**

```bash
git add frontend-advisor/src/agentApi.ts frontend-advisor/src/pages/AgentSettingsPage.tsx frontend-advisor/src/pages/AgentSettingsPage.test.tsx
git commit -m "feat: rebuild agent model settings for three providers"
```

---

### Task 10: 导航与其余用户文案

**Files:**
- Modify: `frontend-advisor/src/components/TopbarNav.tsx` — `'DeepSeek 配置'` → `'模型配置'`
- Modify: `frontend-advisor/src/App.test.tsx` — menuitem 名称同步
- Modify: `frontend-advisor/src/pages/AgentChatPage.tsx` — `检查 DeepSeek 配置…` → `检查模型配置…`
- Modify: `frontend-advisor/src/pages/PolicyWatchPage.tsx` — `DeepSeek 已配置` → `模型已配置`；`未配置 DeepSeek` → `未配置模型`
- Modify: `frontend-advisor/src/pages/LimitUpPromotePage.tsx` — 介绍里「用你的 DeepSeek」→「用你配置的模型」；链接文字「DeepSeek 配置」→「模型配置」；`isMissingKeyError` 改为 `/模型配置|API Key/i`
- Modify: `frontend-advisor/src/pages/LimitUpPromotePage.test.tsx` — 错误文案与链接名改为新字符串（mock reject `请先在模型配置中填写 API Key`，link `模型配置`）

- [ ] **Step 1: 改测试为新文案并确认失败**

Run: `cd frontend-advisor && npm test -- src/App.test.tsx src/pages/LimitUpPromotePage.test.tsx`  
Expected: FAIL（导航仍是 DeepSeek 配置）直到改组件。

- [ ] **Step 2: 改组件文案**

按 Files 列表逐处替换。

- [ ] **Step 3: 跑前端相关测试**

Run: `cd frontend-advisor && npm test -- src/App.test.tsx src/pages/LimitUpPromotePage.test.tsx src/pages/AgentSettingsPage.test.tsx src/pages/AgentChatPage.test.tsx`  
Expected: PASS

- [ ] **Step 4: 全量确认**

```bash
cd backend && python -m pytest tests/test_llm_web_settings.py tests/test_llm_providers.py tests/test_committee_llm.py -q
cd frontend-advisor && npm test
```

Expected: PASS

- [ ] **Step 5: Commit（默认跳过）**

```bash
git add frontend-advisor backend
git commit -m "feat: rename DeepSeek settings copy to model settings"
```

---

## Spec coverage（自检）

| Spec 项 | 任务 |
|---------|------|
| 三家 Key / 目录 / 默认模型 / 预勾 | 1, 3 |
| 旧文档迁移 | 2 |
| GET 公开形状 | 2 |
| PUT/DELETE provider、refresh、第一把 Key 填槽位、清除 remap | 3, 7 |
| PUT slots + enabled_models + 旧 api_key 兼容 | 4, 7 |
| DELETE /settings 清全部提供方留 Tavily | 3 |
| resolve(slot) / web_research 挂载 vs 首页 | 5, 8 |
| build_chat_model slot/tier、Kimi 无 temperature | 6 |
| 八个调用点 slot | 8 |
| 设置页勾选+下拉+切换默认 | 9 |
| 导航与中性文案 | 10 |
| 403 文案 | 7, 10 |
| 委员会不再读 yaml models | 6 |
| 不测外网 | 全程 mock |
