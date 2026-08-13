# Policy Watch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 做出「政策雷达」：预置/自定义栏目发现新文，全量进收件箱，达灵敏度才发邮件；用户可配扫描时段与间隔。

**Architecture:** 独立模块 `backend/app/advisor/policy_watch/`，集合与盯盘任务分离。`monitor-worker` 在 `run_monitor_tick` 末尾带预算调用 `run_policy_watch_tick()`。发现新文是列表抽链 + URL 指纹；LLM 只解读；抓取/解读按 URL 共享，扇出与发信按用户窗口和灵敏度。前端新页 `/agent/policy-watch`。

**Tech Stack:** FastAPI、MongoDB、pytest、现有 `fetch_url_with_escalation` / `build_chat_model` / `send_email`、React 19、Vitest

## Global Constraints

- Spec：`docs/superpowers/specs/2026-08-13-policy-watch-design.md`
- 不写入 `agent_monitor_jobs`；不提供对话 Agent 创建雷达的工具；不提供「立即全量重扫」
- 交易时间：A 股交易日 09:15–15:05（北京时间，含午休），复用 `is_trading_day`
- 自定义源最多 8 条；列表最多抽 20 链；每 tick 最多 4 个源、5 篇精读、8 秒
- 间隔：交易 5–180 默认 15；非交易 15–360 默认 60；越界夹紧，非整数 400
- 开启/新加源：当前列表只标已见，不解读、不发信
- 邮件前缀 `[政策雷达]`；免责声明：研究参考，不构成投资建议
- 镜像标签仍为 `名称:架构`（如 `share-data:amd64`），禁止部署默认用 `latest`
- 计划中的 commit 步骤默认跳过，除非用户明确要求提交

---

### File map

| 文件 | 职责 |
|------|------|
| `backend/app/advisor/config.yaml` | `policy_watch` 限额与预置源 |
| `backend/app/advisor/policy_watch/config.py` | 读 yaml + 默认值 |
| `backend/app/advisor/policy_watch/urls.py` | URL/标题规范化 |
| `backend/app/advisor/policy_watch/schedule.py` | 扫描窗口与间隔 |
| `backend/app/advisor/policy_watch/sensitivity.py` | 发信阈值、方向文案 |
| `backend/app/advisor/policy_watch/settings.py` | 每用户配置读写 |
| `backend/app/advisor/policy_watch/store.py` | seen / articles / items / source_scans |
| `backend/app/advisor/policy_watch/discover.py` | 抽链、结构化源、种子扫描 |
| `backend/app/advisor/policy_watch/interpret.py` | LLM JSON 解析与写回 |
| `backend/app/advisor/policy_watch/mailer.py` | 邮件主题/正文 |
| `backend/app/advisor/policy_watch/fanout.py` | 按用户窗口扇出 + 发信 |
| `backend/app/advisor/policy_watch/tick.py` | 单 tick 编排 |
| `backend/app/advisor/policy_watch/routes.py` | HTTP |
| `backend/app/advisor/policy_watch/__init__.py` | 导出 `run_policy_watch_tick` |
| `backend/app/advisor/monitor/engine.py` | tick 末尾调用 |
| `backend/app/db.py` | 索引 |
| `frontend-advisor/src/api.ts` | 类型与 fetch |
| `frontend-advisor/src/pages/PolicyWatchPage.tsx` | 设置 + 收件箱 |
| `frontend-advisor/src/App.tsx` / `TopbarNav.tsx` | 路由与顶栏 |

---

### Task 1: 纯函数（URL、窗口、灵敏度、配置）

**Files:**
- Create: `backend/app/advisor/policy_watch/__init__.py`
- Create: `backend/app/advisor/policy_watch/config.py`
- Create: `backend/app/advisor/policy_watch/urls.py`
- Create: `backend/app/advisor/policy_watch/schedule.py`
- Create: `backend/app/advisor/policy_watch/sensitivity.py`
- Create: `backend/tests/test_policy_watch_helpers.py`
- Modify: `backend/app/advisor/config.yaml`（文件末尾追加 `policy_watch` 段）

**Interfaces:**
- Consumes: `load_config()`；`is_trading_day`；`ZoneInfo("Asia/Shanghai")`
- Produces:
  - `policy_watch_config() -> dict[str, Any]`
  - `normalize_url_key(url: str) -> str`
  - `normalize_title(title: str) -> str`
  - `titles_similar(a: str, b: str) -> bool`
  - `clamp_interval(value: Any, *, kind: str) -> int`  
    `kind` 为 `"trading"` 或 `"offhours"`；非整数 raise `ValueError("间隔必须是整数")`
  - `in_user_scan_window(settings: dict[str, Any], *, now: datetime | None = None) -> bool`
  - `current_interval_minutes(settings: dict[str, Any], *, now: datetime | None = None) -> int`
  - `user_interval_elapsed(settings: dict[str, Any], *, now: datetime | None = None) -> bool`  
    看 `settings["last_fanout_at"]` 与当前档间隔
  - `should_email(interpretation: dict[str, Any], sensitivity: str) -> bool`
  - `direction_label(direction: str) -> str`  
    `up`→`利好`，`down`→`利空`，`mixed`→`分化`，其它→`影响不明`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_policy_watch_helpers.py
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.advisor.policy_watch.urls import (
    normalize_title,
    normalize_url_key,
    titles_similar,
)
from app.advisor.policy_watch.schedule import (
    clamp_interval,
    current_interval_minutes,
    in_user_scan_window,
    user_interval_elapsed,
)
from app.advisor.policy_watch.sensitivity import direction_label, should_email

SH = ZoneInfo("Asia/Shanghai")


def test_normalize_url_key_strips_tracking():
    a = normalize_url_key("https://www.gov.cn/zhengce/content/2026-08/13/x.htm?utm_source=a&from=b")
    b = normalize_url_key("https://www.gov.cn/zhengce/content/2026-08/13/x.htm")
    assert a == b
    assert a.startswith("https://www.gov.cn/")


def test_titles_similar():
    assert titles_similar("国务院印发新能源指导意见", "国务院印发新能源指导意见 ")
    assert not titles_similar("国务院印发新能源指导意见", "央行下调存款准备金率")


def test_clamp_interval():
    assert clamp_interval(4, kind="trading") == 5
    assert clamp_interval(200, kind="trading") == 180
    assert clamp_interval(10, kind="offhours") == 15
    with pytest.raises(ValueError, match="整数"):
        clamp_interval("x", kind="trading")


def test_scan_window_trading_only_weekend(monkeypatch):
    monkeypatch.setattr(
        "app.advisor.policy_watch.schedule.is_trading_day", lambda _d: False
    )
    now = datetime(2026, 8, 15, 10, 0, tzinfo=SH)  # Saturday
    settings = {
        "scan_mode": "trading_only",
        "interval_trading_min": 15,
        "interval_offhours_min": 60,
    }
    assert in_user_scan_window(settings, now=now) is False


def test_scan_window_always_weekend(monkeypatch):
    monkeypatch.setattr(
        "app.advisor.policy_watch.schedule.is_trading_day", lambda _d: False
    )
    now = datetime(2026, 8, 15, 10, 0, tzinfo=SH)
    settings = {"scan_mode": "always", "interval_trading_min": 15, "interval_offhours_min": 60}
    assert in_user_scan_window(settings, now=now) is True
    assert current_interval_minutes(settings, now=now) == 60


def test_scan_window_trading_hours(monkeypatch):
    monkeypatch.setattr(
        "app.advisor.policy_watch.schedule.is_trading_day", lambda _d: True
    )
    now = datetime(2026, 8, 13, 10, 30, tzinfo=SH)
    settings = {
        "scan_mode": "trading_only",
        "interval_trading_min": 15,
        "interval_offhours_min": 60,
    }
    assert in_user_scan_window(settings, now=now) is True
    assert current_interval_minutes(settings, now=now) == 15
    night = datetime(2026, 8, 13, 20, 0, tzinfo=SH)
    assert in_user_scan_window(settings, now=night) is False


def test_user_interval_elapsed():
    now = datetime(2026, 8, 13, 10, 30, tzinfo=SH)
    settings = {
        "scan_mode": "always",
        "interval_trading_min": 15,
        "interval_offhours_min": 60,
        "last_fanout_at": datetime(2026, 8, 13, 10, 20, tzinfo=SH),
    }
    assert user_interval_elapsed(settings, now=now) is False
    settings["last_fanout_at"] = datetime(2026, 8, 13, 10, 0, tzinfo=SH)
    assert user_interval_elapsed(settings, now=now) is True
    settings["last_fanout_at"] = None
    assert user_interval_elapsed(settings, now=now) is True


def test_should_email_thresholds():
    policy = {"impact_score": 0.76, "category": "policy", "sectors": [], "symbols": []}
    mid = {"impact_score": 0.5, "category": "news", "sectors": [], "symbols": []}
    loose = {
        "impact_score": 0.3,
        "category": "news",
        "sectors": [{"name": "新能源", "reason": "x"}],
        "symbols": [],
    }
    assert should_email(policy, "low") is True
    assert should_email(mid, "low") is False
    assert should_email(mid, "medium") is True
    assert should_email(loose, "high") is True
    assert should_email({"impact_score": 0.3, "category": "news", "sectors": [], "symbols": []}, "high") is False
    assert direction_label("up") == "利好"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest tests/test_policy_watch_helpers.py -v`  
Expected: FAIL（模块不存在）

- [ ] **Step 3: Write minimal implementation**

`__init__.py` 先空文件或只写模块注释。

`config.py`：`policy_watch_config()` 读 `load_config().get("policy_watch") or {}`，缺字段用与 spec 相同的默认值（`max_custom_sources=8`、`max_list_links=20`、`max_fetch_per_tick=5`、`max_sources_per_tick=4`、`max_tick_seconds=8`、`max_article_chars=8000`、`similar_title_hours=24`、间隔上下限与默认、`trading_start="09:15"`、`trading_end="15:05"`）。

`urls.py`：
- `normalize_url_key`：`urlparse`，去掉 fragment；query 丢掉 `utm_*`、`from`、`spm`、`ref`；scheme/host 小写；去掉默认端口；path 去尾 `/`（根路径除外）。
- `normalize_title`：去空白、全角空格，`casefold`。
- `titles_similar`：归一化后相等，或较短一方长度≥8 且是较长一方的子串。

`schedule.py`：
- 用 `ZoneInfo("Asia/Shanghai")` 把 `now` 转到上海。
- 交易时段：`is_trading_day(上海日期)` 且 `trading_start <= HH:MM <= trading_end`（含午休）。
- `scan_mode=always`：窗口恒 True，间隔按是否交易时段选档。
- `trading_only` / `offhours_only`：只在对应半区为 True。
- `user_interval_elapsed`：`last_fanout_at` 为空或差值 ≥ 当前档分钟数。

`sensitivity.py`：按 spec 表实现 `should_email`。`symbols` 里 `verified is False` 的不算「已核实个股」。高档需要至少一个板块或一只 `verified is not False` 且有 `symbol` 的个股。

`config.yaml` 末尾追加 spec 中的 `policy_watch:` 段（`scio_news.list_url` 用 `https://www.scio.gov.cn/xwfb/`）。

- [ ] **Step 4: Run tests**

Run: `cd backend && .venv/bin/python -m pytest tests/test_policy_watch_helpers.py -v`  
Expected: PASS

- [ ] **Step 5: Commit（默认跳过）**

```bash
git add backend/app/advisor/policy_watch backend/app/advisor/config.yaml backend/tests/test_policy_watch_helpers.py
git commit -m "feat: add policy watch helper functions"
```

---

### Task 2: 用户设置读写

**Files:**
- Create: `backend/app/advisor/policy_watch/settings.py`
- Create: `backend/tests/test_policy_watch_settings.py`

**Interfaces:**
- Consumes: Task 1 的 `clamp_interval`、`policy_watch_config`；`is_url_safe_for_fetch(url, allowed_ports=[80, 443])`；`require_verified_email` 的只读变体
- Produces:
  - `DEFAULT_PRESET_IDS = ["gov_zhengce", "scio_news"]`
  - `default_settings(user_id: str) -> dict[str, Any]`  
    `enabled=False`，`sensitivity="medium"`，`scan_mode="always"`，间隔默认 15/60，`preset_ids=DEFAULT_PRESET_IDS`，`custom_sources=[]`，`notify_email=None`，`source_status={}`，`last_fanout_at=None`，`last_error=None`
  - `public_settings(doc: dict[str, Any]) -> dict[str, Any]`（ISO 时间，去掉内部 Mongo `_id`）
  - `get_settings(user_id: str) -> dict[str, Any]`：无文档返回 `default_settings`（不写库）
  - `update_settings(user_id: str, body: dict[str, Any]) -> dict[str, Any]`
  - `list_enabled_settings() -> list[dict[str, Any]]`
  - `peek_verified_email(user_id: str) -> str | None`：验证失败返回 `None`，不抛
  - `touch_settings(user_id: str, **fields) -> None`
  - 自定义源每项：`{id, url, title?}`；`id` 用短 uuid
  - `enabled` false→true 或新增 `preset_ids`/`custom_sources`：对应 `source_status[source_key].state = "seeding"`
  - 自定义第 9 条或 SSRF 失败 → `ValueError`（路由变 400）

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_policy_watch_settings.py
from app.advisor.policy_watch import settings as settings_mod


class _Coll:
    def __init__(self):
        self.docs = []

    def find_one(self, q, proj=None):
        for d in self.docs:
            if d.get("user_id") == q.get("user_id"):
                return dict(d)
        return None

    def find(self, q):
        if q.get("enabled") is True:
            return [dict(d) for d in self.docs if d.get("enabled")]
        return [dict(d) for d in self.docs]

    def update_one(self, q, update, upsert=False):
        doc = self.find_one(q)
        body = update.get("$set") or {}
        if doc is None:
            if not upsert:
                return
            doc = {"user_id": q.get("user_id")}
            self.docs.append(doc)
        doc.update(body)


class _DB:
    def __init__(self):
        self.policy_watch_settings = _Coll()


def test_get_settings_defaults(monkeypatch):
    monkeypatch.setattr(settings_mod, "get_db", lambda: _DB())
    s = settings_mod.get_settings("u1")
    assert s["enabled"] is False
    assert s["sensitivity"] == "medium"
    assert s["preset_ids"] == ["gov_zhengce", "scio_news"]
    assert s["interval_trading_min"] == 15


def test_update_clamps_and_rejects_ninth_url(monkeypatch):
    db = _DB()
    monkeypatch.setattr(settings_mod, "get_db", lambda: db)
    monkeypatch.setattr(settings_mod, "peek_verified_email", lambda _uid: "a@b.c")
    monkeypatch.setattr(
        settings_mod,
        "is_url_safe_for_fetch",
        lambda url, allowed_ports=None: (True, ""),
    )
    out = settings_mod.update_settings("u1", {"interval_trading_min": 4, "enabled": True})
    assert out["interval_trading_min"] == 5
    assert out["notify_email"] == "a@b.c"
    assert out["source_status"]["gov_zhengce"]["state"] == "seeding"
    customs = [{"url": f"https://example.com/list/{i}"} for i in range(9)]
    try:
        settings_mod.update_settings("u1", {"custom_sources": customs})
        raise AssertionError("expected ValueError")
    except ValueError as exc:
        assert "8" in str(exc)


def test_reject_localhost(monkeypatch):
    monkeypatch.setattr(settings_mod, "get_db", lambda: _DB())
    monkeypatch.setattr(settings_mod, "peek_verified_email", lambda _uid: None)
    monkeypatch.setattr(
        settings_mod,
        "is_url_safe_for_fetch",
        lambda url, allowed_ports=None: (False, "禁止：目标为内网或本机地址"),
    )
    try:
        settings_mod.update_settings(
            "u1", {"custom_sources": [{"url": "http://127.0.0.1/"}]}
        )
        raise AssertionError("expected ValueError")
    except ValueError as exc:
        assert "禁止" in str(exc)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest tests/test_policy_watch_settings.py -v`  
Expected: FAIL

- [ ] **Step 3: Write `settings.py`**

- `get_db().policy_watch_settings`
- `peek_verified_email`：`try/except` 包 `require_verified_email`，失败返回 `None`
- `update_settings` 先 `get_settings` 再合并：只更新 body 里出现的键
- `custom_sources`：对每条 URL `strip`，调用 `is_url_safe_for_fetch`；超过 `policy_watch_config()["max_custom_sources"]` 抛 `ValueError("自定义栏目最多 8 条")`
- `source_key`：预置用 id；自定义用 `normalize_url_key(url)`
- 比较旧 `preset_ids`+自定义 URL 集合与新集合，新增的标 `seeding`
- `enabled` 从 False→True：当前所有源都标 `seeding`
- 写回 `updated_at=datetime.now(timezone.utc)`，`upsert=True`

- [ ] **Step 4: Run tests**

Run: `cd backend && .venv/bin/python -m pytest tests/test_policy_watch_settings.py tests/test_policy_watch_helpers.py -v`  
Expected: PASS

- [ ] **Step 5: Commit（默认跳过）**

```bash
git add backend/app/advisor/policy_watch/settings.py backend/tests/test_policy_watch_settings.py
git commit -m "feat: persist policy watch user settings"
```

---

### Task 3: seen / articles / items / 共享扫描状态

**Files:**
- Create: `backend/app/advisor/policy_watch/store.py`
- Create: `backend/tests/test_policy_watch_store.py`
- Modify: `backend/app/db.py`（`ensure_indexes` 末尾 `try` 块增加四个集合索引）

**Interfaces:**
- Consumes: `normalize_url_key`、`normalize_title`
- Produces:
  - `mark_seen(source_key: str, url_key: str, title: str, *, now=None) -> bool`  
    已存在返回 `False`；新插入返回 `True`
  - `seed_seen(source_key: str, links: list[dict[str, Any]], *, now=None) -> int`  
    对每条 `{url, title}` 调 `mark_seen`，返回新标数
  - `upsert_article(*, url, title, source_key, source_label, body_excerpt, body_ok, now=None) -> dict`  
    按 `url_key` upsert；已有文档不覆盖已 `ready` 的 `interpretation`
    返回含 `id`（`str(_id)`）的公开 dict
  - `get_article(article_id: str) -> dict | None`
  - `get_article_by_url_key(url_key: str) -> dict | None`
  - `list_pending_interpret(*, limit: int) -> list[dict]`  
    `interpret_status in {pending, failed}` 且 `interpret_attempts < 2`
  - `save_interpretation(url_key: str, interpretation: dict | None, status: str) -> None`  
    `failed` 时 `interpret_attempts += 1`
  - `insert_item(user_id: str, article_id: str, notify_status: str, *, notified_at=None) -> dict | None`  
    已存在返回 `None`
  - `user_has_item(user_id: str, article_id: str) -> bool`
  - `list_unfanned_articles(user_id: str, source_keys: list[str]) -> list[dict]`  
    `source_key in source_keys` 且该用户还没有 item
  - `list_items(user_id: str, *, filter: str, cursor: str | None, limit: int) -> dict`  
    返回 `{items, next_cursor}`；`filter=emailed` 仅 `notify_status=sent`；`inbox` 为非 `sent`
  - `mark_item_read(user_id: str, item_id: str) -> dict`  
    找不到 raise `ValueError`
  - `recent_notified_titles(user_id: str, source_key: str, *, hours: int = 24) -> list[str]`
  - `get_source_scan(source_key: str) -> dict | None`
  - `touch_source_scan(source_key: str, **fields) -> None`

集合：`policy_watch_seen`、`policy_watch_articles`、`policy_watch_items`、`policy_watch_source_scans`。

索引（`db.py`）：
- `policy_watch_settings`：`user_id` unique
- `policy_watch_articles`：`url_key` unique
- `policy_watch_items`：`(user_id, article_id)` unique；`(user_id, created_at desc)`
- `policy_watch_seen`：`(source_key, url_key)` unique
- `policy_watch_source_scans`：`source_key` unique

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_policy_watch_store.py
from app.advisor.policy_watch import store as store_mod


class _Coll:
    def __init__(self):
        self.docs = []
        self._n = 0

    def find_one(self, q, proj=None):
        for d in self.docs:
            if all(d.get(k) == v for k, v in q.items() if not isinstance(v, dict)):
                return dict(d)
        return None

    def insert_one(self, doc):
        self._n += 1
        body = dict(doc)
        body.setdefault("_id", f"id{self._n}")
        self.docs.append(body)
        return type("R", (), {"inserted_id": body["_id"]})()

    def update_one(self, q, update, upsert=False):
        doc = self.find_one(q)
        if doc is None and upsert:
            doc = dict(q)
            self.docs.append(doc)
        if doc:
            real = next(d for d in self.docs if d.get("_id") == doc.get("_id") or d is doc)
            real.update(update.get("$set") or {})
            inc = update.get("$inc") or {}
            for k, v in inc.items():
                real[k] = int(real.get(k) or 0) + int(v)

    def find(self, q):
        out = []
        for d in self.docs:
            ok = True
            for k, v in q.items():
                if k == "source_key" and isinstance(v, dict) and "$in" in v:
                    if d.get("source_key") not in v["$in"]:
                        ok = False
                elif d.get(k) != v:
                    ok = False
            if ok:
                out.append(dict(d))
        return out


class _DB:
    def __init__(self):
        self.policy_watch_seen = _Coll()
        self.policy_watch_articles = _Coll()
        self.policy_watch_items = _Coll()
        self.policy_watch_source_scans = _Coll()


def test_seed_seen_idempotent(monkeypatch):
    db = _DB()
    monkeypatch.setattr(store_mod, "get_db", lambda: db)
    links = [{"url": "https://www.gov.cn/a.htm", "title": "新政"}]
    assert store_mod.seed_seen("gov_zhengce", links) == 1
    assert store_mod.seed_seen("gov_zhengce", links) == 0


def test_article_and_item_unique(monkeypatch):
    db = _DB()
    monkeypatch.setattr(store_mod, "get_db", lambda: db)
    a1 = store_mod.upsert_article(
        url="https://www.gov.cn/a.htm",
        title="新政",
        source_key="gov_zhengce",
        source_label="政府网",
        body_excerpt="正文",
        body_ok=True,
    )
    a2 = store_mod.upsert_article(
        url="https://www.gov.cn/a.htm?utm_source=x",
        title="新政",
        source_key="gov_zhengce",
        source_label="政府网",
        body_excerpt="正文2",
        body_ok=True,
    )
    assert a1["id"] == a2["id"]
    first = store_mod.insert_item("u1", a1["id"], "sent")
    assert first is not None
    assert store_mod.insert_item("u1", a1["id"], "sent") is None
    assert store_mod.list_unfanned_articles("u1", ["gov_zhengce"]) == []
    listed = store_mod.list_items("u1", filter="emailed", cursor=None, limit=30)
    assert len(listed["items"]) == 1
    assert listed["items"][0]["notify_status"] == "sent"
```

- [ ] **Step 2: Run to see FAIL**

Run: `cd backend && .venv/bin/python -m pytest tests/test_policy_watch_store.py -v`

- [ ] **Step 3: Implement `store.py` + indexes**

公开文章/条目字段用 ISO 时间。`list_items` 的 `cursor` 为上一页最后一条 `created_at` 的 ISO，查询 `created_at < cursor`。`limit` 夹到 1–50，默认 30。

- [ ] **Step 4: Run tests**

Run: `cd backend && .venv/bin/python -m pytest tests/test_policy_watch_store.py -v`  
Expected: PASS

- [ ] **Step 5: Commit（默认跳过）**

```bash
git add backend/app/advisor/policy_watch/store.py backend/app/db.py backend/tests/test_policy_watch_store.py
git commit -m "feat: add policy watch article and inbox stores"
```

---

### Task 4: 发现新文（抽链、预置源、种子扫描）

**Files:**
- Create: `backend/app/advisor/policy_watch/discover.py`
- Create: `backend/tests/test_policy_watch_discover.py`

**Interfaces:**
- Consumes: `fetch_url_with_escalation`；`fetch_market_cctv_news` / `fetch_macro_china_snapshot`；`seed_seen` / `mark_seen` / `upsert_article` / `touch_source_scan` / `get_source_scan`；`policy_watch_config`；`list_enabled_settings`；`in_user_scan_window`；`current_interval_minutes`
- Produces:
  - `extract_article_links(html: str, page_url: str, *, max_links: int = 20) -> list[dict[str, str]]`  
    每项 `{url, title}`。只保留与 `page_url` 同 host 的 http(s) 链接。像文章：path 含 `\d{4}` 或 `content`/`zhengce`/`xwfb`/`n/`，或 title 长度 ≥ 8。去重按 `normalize_url_key`。
  - `structured_links(preset_id: str) -> list[dict[str, str]]`  
    `cctv`：`items` 里取 `title`，`url` 用 `policy://cctv/{date}/{normalize_title(title)}`  
    `macro`：把各 `blocks` 最新一条做成 `{title, url: policy://macro/{block}/{title_norm}}`，title 含块名+摘要
  - `fetch_list_html(url: str) -> str`：调 `fetch_url_with_escalation`；若返回以 `错误：` 开头则 raise `RuntimeError`
  - `source_due(source_key: str, interval_min: int, *, now=None) -> bool`  
    `get_source_scan` 的 `last_fetch_at` 为空或已超过间隔
  - `collect_due_source_keys(*, now=None) -> list[dict[str, Any]]`  
    遍历 `list_enabled_settings()`，只统计 `in_user_scan_window` 的用户；每个源的间隔取这些用户 `current_interval_minutes` 的最小值，再与全局地板（交易 5 / 非交易 15，按**现在是否交易时段**选）取 max；返回最多 `max_sources_per_tick` 个到期源  
    每项：`{source_key, kind: "preset"|"custom", preset_id?, url?, label, interval_min, seeding: bool}`  
    `seeding`：任一订阅用户的 `source_status[source_key].state=="seeding"`
  - `ingest_source(spec: dict[str, Any], *, now=None) -> dict[str, Any]`  
    取 links → 若 `seeding`：只 `seed_seen`，把相关用户该源 `state` 改为 `ok`，**不** `upsert_article`  
    否则：对 `mark_seen` 为新的链接，若未达 `max_fetch_per_tick` 则抓正文（`policy://` 不抓网页，`body_ok=True` 用 title 当 excerpt），`upsert_article`（`interpret_status=pending`）  
    正文失败或像 PDF（url 以 `.pdf` 结尾或 fetch 文本 < 40 字）：`body_ok=False`，`body_excerpt=None`  
    抽链为空：`touch_source_scan(..., last_error="该页不像列表，请换栏目 URL")`，并写回订阅用户 `source_status`
    返回 `{new_articles, seeded, error}`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_policy_watch_discover.py
from app.advisor.policy_watch.discover import extract_article_links, ingest_source


HTML = """
<html><body>
<a href="/zhengce/content/2026-08/13/c_1.htm">国务院印发指导意见</a>
<a href="/home">首页</a>
<a href="https://evil.com/x">外站</a>
</body></html>
"""


def test_extract_same_host_article_links():
    links = extract_article_links(HTML, "https://www.gov.cn/zhengce/zuixin/", max_links=20)
    assert len(links) == 1
    assert links[0]["url"].startswith("https://www.gov.cn/zhengce/content/")
    assert "指导意见" in links[0]["title"]


def test_seed_does_not_create_article(monkeypatch):
    seen = []
    articles = []

    monkeypatch.setattr(
        "app.advisor.policy_watch.discover.fetch_list_html",
        lambda _url: HTML,
    )
    monkeypatch.setattr(
        "app.advisor.policy_watch.discover.seed_seen",
        lambda sk, links, now=None: seen.append(links) or len(links),
    )
    monkeypatch.setattr(
        "app.advisor.policy_watch.discover.upsert_article",
        lambda **kw: articles.append(kw) or {"id": "a1"},
    )
    monkeypatch.setattr(
        "app.advisor.policy_watch.discover.touch_source_scan", lambda *a, **k: None
    )
    monkeypatch.setattr(
        "app.advisor.policy_watch.discover.clear_seeding", lambda *a, **k: None
    )
    out = ingest_source(
        {
            "source_key": "gov_zhengce",
            "kind": "preset",
            "preset_id": "gov_zhengce",
            "url": "https://www.gov.cn/zhengce/zuixin/",
            "label": "中国政府网 · 最新政策",
            "seeding": True,
        }
    )
    assert out["seeded"] >= 1
    assert articles == []
```

再写一个 `seeding=False` 时会 `upsert_article` 的测试：`mark_seen` mock 返回 `True`，`fetch_url_with_escalation` mock 返回一段正文。

`clear_seeding(source_key: str) -> None`：遍历 enabled settings，把该 key 的 `state` 从 `seeding` 改为 `ok`（在 `settings.py` 增加此函数，本任务一并实现）。

- [ ] **Step 2: Run to see FAIL**

Run: `cd backend && .venv/bin/python -m pytest tests/test_policy_watch_discover.py -v`

- [ ] **Step 3: Implement `discover.py`**

抽链用 `html.parser` 或正则 `<a href>`，`urllib.parse.urljoin`。预置 URL 来自 `policy_watch_config()["presets"][id]["list_url"]`。`cctv`/`macro` 的 `kind=preset` 且无 `list_url` 时走 `structured_links`。

- [ ] **Step 4: Run tests**

Run: `cd backend && .venv/bin/python -m pytest tests/test_policy_watch_discover.py -v`  
Expected: PASS

- [ ] **Step 5: Commit（默认跳过）**

```bash
git add backend/app/advisor/policy_watch/discover.py backend/app/advisor/policy_watch/settings.py backend/tests/test_policy_watch_discover.py
git commit -m "feat: discover new policy-watch list entries"
```

---

### Task 5: LLM 解读

**Files:**
- Create: `backend/app/advisor/policy_watch/interpret.py`
- Create: `backend/tests/test_policy_watch_interpret.py`

**Interfaces:**
- Consumes: `build_chat_model`；`save_interpretation`；`list_pending_interpret`；`list_enabled_settings`；`public_llm_settings`；`normalize_symbol`（`app.kline`）
- Produces:
  - `parse_interpretation(text: str) -> dict[str, Any]`  
    剥 markdown 围栏；必须含可转 float 的 `impact_score`，否则 raise `ValueError`  
    夹紧 score 到 0–1；`direction` 只允许四值；`sectors`≤5；`symbols`≤8
  - `verify_symbols(symbols: list[dict[str, Any]]) -> list[dict[str, Any]]`  
    有 6 位代码（`normalize_symbol` 成功）→ `verified=True`，`symbol` 为 6 位  
    只有名称 → `verified=False`，`name` 后可保留，`symbol` 可空  
    两者都无 → 丢弃
  - `pick_interpret_user_id() -> str | None`  
    第一个 `public_llm_settings(uid)["configured"]` 为 True 的 enabled 用户
  - `interpret_pending(*, limit: int | None = None) -> dict[str, int]`  
    对 pending 文章：无解读用户则跳过；有则 `build_chat_model(user_id, temperature=0.1, streaming=False)`  
    system：只输出 JSON，字段与 spec 一致，勿编造代码，研究观察非投资建议  
    human：来源、标题、正文（截到 `max_article_chars`）  
    成功：`verify_symbols` 后 `save_interpretation(..., "ready")`  
    异常：`save_interpretation(url_key, None, "failed")`

- [ ] **Step 1: Write the failing test**

覆盖：`parse_interpretation` 正常 JSON；围栏 JSON；缺 `impact_score` 抛错；`verify_symbols` 丢掉 `"苹果公司"` 无代码项、保留 `300750`；`interpret_pending` 在 mock `build_chat_model.invoke` 后把 status 写成 ready（通过 spy `save_interpretation`）。

- [ ] **Step 2: Run to see FAIL**

Run: `cd backend && .venv/bin/python -m pytest tests/test_policy_watch_interpret.py -v`

- [ ] **Step 3: Implement**

- [ ] **Step 4: Run tests**

Run: `cd backend && .venv/bin/python -m pytest tests/test_policy_watch_interpret.py -v`  
Expected: PASS

- [ ] **Step 5: Commit（默认跳过）**

```bash
git add backend/app/advisor/policy_watch/interpret.py backend/tests/test_policy_watch_interpret.py
git commit -m "feat: interpret policy-watch articles with structured LLM JSON"
```

---

### Task 6: 扇出与邮件

**Files:**
- Create: `backend/app/advisor/policy_watch/mailer.py`
- Create: `backend/app/advisor/policy_watch/fanout.py`
- Create: `backend/tests/test_policy_watch_fanout.py`

**Interfaces:**
- Consumes: `should_email`、`direction_label`、`titles_similar`；`in_user_scan_window`、`user_interval_elapsed`；`list_unfanned_articles`、`insert_item`、`recent_notified_titles`、`touch_settings`；`send_email`；`peek_verified_email`
- Produces:
  - `build_policy_watch_email(rows: list[dict[str, Any]]) -> tuple[str, str]`  
    `rows` 每项含 `title, source_label, url, summary, direction, sectors, symbols, body_ok`  
    单篇主题：`[政策雷达] {方向} · {短标题}`  
    多篇：`[政策雷达] {N}条可能影响市场 · {首条短标题}`  
    正文含来源、原文、摘要、方向、板块、个股（`verified is False` 标明待核实）、`body_ok is False` 时「仅依据标题」、末行「研究参考，不构成投资建议。」
  - `should_skip_similar(user_id: str, source_key: str, title: str) -> bool`
  - `fanout_user(settings: dict[str, Any], *, now=None) -> dict[str, int]`  
    不在窗口或间隔未到：`{items:0, emailed:0, skipped:1}`  
    源集合 = 当前 `preset_ids` + 自定义 URL keys  
    对 `list_unfanned_articles`：无解读或 `interpret_status!="ready"` → `insert_item(..., "skipped")`  
    有解读但 `should_email` 为假或 `should_skip_similar` → `skipped`  
    否则列入待发  
    待发非空且 `settings["notify_email"]` 或 `peek_verified_email` 有值：`send_email` 一次；成功则这些 item `notify_status=sent` 并 `notified_at=now`；`SMTP`/函数异常则全部 `failed`，`touch_settings(last_error=...)`，**不要下一 tick 重发**（item 已落下 `failed`）  
    无邮箱：待发也落 `skipped`  
    最后 `touch_settings(last_fanout_at=now)`（只要进了窗口且间隔已到，即使 0 篇新文也更新，避免空转）
  - `fanout_due_users(*, now=None) -> dict[str, int]`：对 `list_enabled_settings()` 逐个 `fanout_user`，吞掉单用户异常

- [ ] **Step 1: Write the failing test**

1. `build_policy_watch_email` 单篇主题含 `[政策雷达]` 与 `利好`，正文含免责与原文 URL
2. 两篇 → 主题含 `2条`
3. `fanout_user`：mock 一篇 `impact_score=0.8, category=policy`，`sensitivity=medium`，有邮箱 → `send_email` 被调用 1 次，`insert_item` 为 `sent`
4. 同一用户再 fanout 该文：`list_unfanned` 为空 → 不再发信
5. `sensitivity=low` 且 `category=news, score=0.5` → `skipped`，不发信
6. `scan_mode=trading_only` + 周末 now → 不 `insert_item`
7. `send_email` 抛错 → item `failed`，不在第二次 fanout 重发（第二次 unfanned 已空）

- [ ] **Step 2: Run to see FAIL**

Run: `cd backend && .venv/bin/python -m pytest tests/test_policy_watch_fanout.py -v`

- [ ] **Step 3: Implement mailer + fanout**

- [ ] **Step 4: Run tests**

Run: `cd backend && .venv/bin/python -m pytest tests/test_policy_watch_fanout.py -v`  
Expected: PASS

- [ ] **Step 5: Commit（默认跳过）**

```bash
git add backend/app/advisor/policy_watch/mailer.py backend/app/advisor/policy_watch/fanout.py backend/tests/test_policy_watch_fanout.py
git commit -m "feat: fan out policy-watch inbox items and alert email"
```

---

### Task 7: Tick 编排并挂上 monitor-worker

**Files:**
- Create: `backend/app/advisor/policy_watch/tick.py`
- Modify: `backend/app/advisor/policy_watch/__init__.py`（导出 `run_policy_watch_tick`）
- Modify: `backend/app/advisor/monitor/engine.py`（`run_monitor_tick` 在 signal_graph 之后、`return stats` 之前加 try/except）
- Modify: `backend/app/advisor/monitor/worker.py`（日志增加 `policy_watch` 计数）
- Create: `backend/tests/test_policy_watch_tick.py`
- Modify: `backend/tests/test_monitor_engine.py`（现有 tick 测试需 mock `run_policy_watch_tick`，避免连真实雷达）

**Interfaces:**
- Consumes: `collect_due_source_keys`、`ingest_source`、`interpret_pending`、`fanout_due_users`；`policy_watch_config()["max_tick_seconds"]` / `max_fetch_per_tick`
- Produces:
  - `run_policy_watch_tick(*, now=None, started: float | None = None) -> dict[str, int]`  
    键：`sources, articles, interpreted, items, emailed, errors`  
    顺序：到期源 ingest（累计精读篇数到上限则停止抓正文，仍可 seed）→ `interpret_pending` → `fanout_due_users`  
    `time.monotonic() - started > max_tick_seconds` 则中断后续阶段  
    单步异常：`errors += 1`，继续
  - `engine.run_monitor_tick` 增加 `stats["policy_watch"] = run_policy_watch_tick()`；异常则 `policy_watch={"errors":1}` 且 `stats["errors"] += 1`，**不得**让盯盘统计丢失

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_policy_watch_tick.py
from app.advisor.policy_watch import tick as tick_mod


def test_tick_order_and_budget(monkeypatch):
    calls = []
    monkeypatch.setattr(tick_mod, "policy_watch_config", lambda: {
        "max_tick_seconds": 8,
        "max_fetch_per_tick": 5,
    })
    monkeypatch.setattr(
        tick_mod,
        "collect_due_source_keys",
        lambda now=None: calls.append("collect") or [
            {"source_key": "gov_zhengce", "seeding": False}
        ],
    )
    monkeypatch.setattr(
        tick_mod,
        "ingest_source",
        lambda spec, now=None: calls.append("ingest") or {"new_articles": 1, "seeded": 0, "error": None},
    )
    monkeypatch.setattr(
        tick_mod,
        "interpret_pending",
        lambda limit=None: calls.append("interpret") or {"ok": 1, "failed": 0},
    )
    monkeypatch.setattr(
        tick_mod,
        "fanout_due_users",
        lambda now=None: calls.append("fanout") or {"items": 1, "emailed": 1},
    )
    out = tick_mod.run_policy_watch_tick()
    assert calls == ["collect", "ingest", "interpret", "fanout"]
    assert out["emailed"] == 1


def test_engine_swallows_policy_watch(monkeypatch):
    from app.advisor.monitor import engine as engine_mod

    monkeypatch.setattr(engine_mod, "activate_due_jobs", lambda now=None: {})
    monkeypatch.setattr(engine_mod, "finalize_watch_windows", lambda now=None: {})
    monkeypatch.setattr(
        "app.quote.trading_session", lambda: {"is_trading": False}
    )
    monkeypatch.setattr(
        "app.advisor.paper_trader.scheduler.run_due_paper_traders",
        lambda now=None: {},
    )
    monkeypatch.setattr(
        "app.advisor.paper_trader.scheduler.finalize_paper_trader_day_ends",
        lambda now=None: 0,
    )
    monkeypatch.setattr(
        "app.advisor.signal_graph.evolve.run_daily_evolve",
        lambda now=None: {"ok": True, "skipped": "test"},
    )
    monkeypatch.setattr(
        "app.advisor.policy_watch.run_policy_watch_tick",
        lambda **k: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    stats = engine_mod.run_monitor_tick()
    assert stats["errors"] >= 1
    assert stats.get("policy_watch", {}).get("errors") == 1
```

给 `test_monitor_engine.py` 的 autouse fixture 增加：

```python
monkeypatch.setattr(
    "app.advisor.policy_watch.run_policy_watch_tick",
    lambda **_k: {"sources": 0, "articles": 0, "interpreted": 0, "items": 0, "emailed": 0, "errors": 0},
)
```

若 import 循环，engine 内使用 `from ..policy_watch import run_policy_watch_tick`（与 paper_trader 一样局部 import）。

- [ ] **Step 2: Run to see FAIL**

Run: `cd backend && .venv/bin/python -m pytest tests/test_policy_watch_tick.py tests/test_monitor_engine.py -v`

- [ ] **Step 3: Implement tick + engine hook + worker log**

`worker.py` 的 info 日志追加 `policy_watch=%s`，值为 `stats.get("policy_watch")`。

- [ ] **Step 4: Run tests**

Run: `cd backend && .venv/bin/python -m pytest tests/test_policy_watch_tick.py tests/test_monitor_engine.py -v`  
Expected: PASS

- [ ] **Step 5: Commit（默认跳过）**

```bash
git add backend/app/advisor/policy_watch/tick.py backend/app/advisor/policy_watch/__init__.py backend/app/advisor/monitor/engine.py backend/app/advisor/monitor/worker.py backend/tests/test_policy_watch_tick.py backend/tests/test_monitor_engine.py
git commit -m "feat: run policy watch at the end of each monitor tick"
```

---

### Task 8: HTTP API

**Files:**
- Create: `backend/app/advisor/policy_watch/routes.py`
- Modify: `backend/app/advisor/routes.py`（`router.include_router(policy_watch_router)`，与 signal_graph 相同）
- Create: `backend/tests/test_policy_watch_routes.py`

**Interfaces:**
- Consumes: `get_settings` / `update_settings` / `public_settings`；`list_items` / `mark_item_read`；`policy_watch_config()["presets"]`；`public_llm_settings`
- Produces: `APIRouter(prefix="/policy-watch", tags=["policy-watch"])`  
  挂到已有 `/api/advisor` 下，最终路径：
  - `GET /api/advisor/policy-watch/presets` → `{presets:[{id,name,description,list_url?}]}`  
    `cctv`/`macro` 无 `list_url`；description 用中文一句
  - `GET /api/advisor/policy-watch/settings` → settings + `llm_configured: bool` + `email_verified: bool`
  - `PUT /api/advisor/policy-watch/settings` body 任意子集；`ValueError`→400
  - `GET /api/advisor/policy-watch/items?filter=all&cursor=&limit=30`
  - `POST /api/advisor/policy-watch/items/{item_id}/read`  
    `ValueError`→404
  均 `Depends(get_current_user)` + `_bind`（在 advisor `routes.py` 里写薄包装，或在 policy_watch routes 里自己 `context.bind_user`）

为少改 `routes.py`，在 `policy_watch/routes.py` 内：

```python
from ...auth import get_current_user
from .. import context

router = APIRouter(prefix="/policy-watch", tags=["policy-watch"])

def _uid(user: dict = Depends(get_current_user)) -> str:
    uid = str(user["id"])
    context.bind_user(uid)
    return uid
```

`advisor/routes.py` 只加：

```python
from .policy_watch.routes import router as policy_watch_router
router.include_router(policy_watch_router)
```

- [ ] **Step 1: Write the failing test**

仿 `test_home_news.py`：未登录 GET presets → 401。  
登录后（mock `get_current_user` + mock `get_settings`/`update_settings`）：
- GET settings 含默认 `sensitivity=medium`
- PUT `{interval_trading_min:4}` 返回夹紧后的 5（mock update 真实函数更好：用 Task 2 假 DB）
- PUT 第 9 条自定义 URL → 400
- PUT `http://127.0.0.1/` → 400（可用真实 `is_url_safe_for_fetch`，127.0.0.1 会被拒）

- [ ] **Step 2: Run to see FAIL**

Run: `cd backend && .venv/bin/python -m pytest tests/test_policy_watch_routes.py -v`

- [ ] **Step 3: Implement routes + include**

- [ ] **Step 4: Run tests**

Run: `cd backend && .venv/bin/python -m pytest tests/test_policy_watch_routes.py tests/test_policy_watch_settings.py -v`  
Expected: PASS

- [ ] **Step 5: Commit（默认跳过）**

```bash
git add backend/app/advisor/policy_watch/routes.py backend/app/advisor/routes.py backend/tests/test_policy_watch_routes.py
git commit -m "feat: add policy-watch HTTP settings and inbox APIs"
```

---

### Task 9: 前端政策雷达页

**Files:**
- Modify: `frontend-advisor/src/api.ts`（追加类型与 4 个 fetch）
- Modify: `frontend-advisor/src/components/TopbarNav.tsx`（`AGENT_NAV_LINKS` 在 `/agent/jobs` 后插入 `{ to: '/agent/policy-watch', label: '政策雷达' }`）
- Modify: `frontend-advisor/src/components/TopbarNav.test.tsx`
- Modify: `frontend-advisor/src/App.tsx`（在 jobs 路由后加 PolicyWatchPage；须写在 `/agent/*` 通配**之前**）
- Create: `frontend-advisor/src/pages/PolicyWatchPage.tsx`
- Create: `frontend-advisor/src/pages/PolicyWatchPage.test.tsx`
- Modify: `frontend-advisor/src/styles.css`（仅当现有 class 不够时加 `.policy-watch` 少量规则：来源勾选栅格、inbox 卡片间距）
- Modify: `README.md` 顾问前端表格加一行：政策雷达 `/agent/policy-watch`

**Interfaces:**
- Consumes: `authFetch` from `./auth`
- Produces（`api.ts`）：

```ts
export type PolicyWatchSensitivity = 'low' | 'medium' | 'high'
export type PolicyWatchScanMode = 'always' | 'trading_only' | 'offhours_only'
export type PolicyWatchNotifyStatus = 'skipped' | 'sent' | 'failed'

export type PolicyWatchSettings = {
  enabled: boolean
  sensitivity: PolicyWatchSensitivity
  scan_mode: PolicyWatchScanMode
  interval_trading_min: number
  interval_offhours_min: number
  preset_ids: string[]
  custom_sources: { id: string; url: string; title?: string }[]
  notify_email?: string | null
  source_status?: Record<string, { state?: string; last_ok_at?: string; last_error?: string }>
  last_error?: string | null
  llm_configured?: boolean
  email_verified?: boolean
}

export type PolicyWatchPreset = {
  id: string
  name: string
  description?: string
  list_url?: string
}

export type PolicyWatchItem = {
  id: string
  article_id: string
  title: string
  source_label: string
  url: string
  created_at: string
  summary?: string
  direction?: string
  impact_score?: number
  sectors?: { name: string; reason?: string }[]
  symbols?: { symbol?: string; name?: string; reason?: string; verified?: boolean }[]
  notify_status: PolicyWatchNotifyStatus
  body_ok?: boolean
  read_at?: string | null
}

export function fetchPolicyWatchPresets(): Promise<{ presets: PolicyWatchPreset[] }>
export function fetchPolicyWatchSettings(): Promise<PolicyWatchSettings>
export function savePolicyWatchSettings(body: Partial<PolicyWatchSettings>): Promise<PolicyWatchSettings>
export function fetchPolicyWatchItems(opts?: {
  filter?: 'all' | 'emailed' | 'inbox'
  cursor?: string
  limit?: number
}): Promise<{ items: PolicyWatchItem[]; next_cursor?: string | null }>
export function markPolicyWatchItemRead(id: string): Promise<PolicyWatchItem>
```

页面行为：
- 顶栏开关、灵敏度三档、扫描时段三选一、两个间隔数字（按 mode 灰掉另一档）
- 预置 checkbox；自定义 URL 输入+添加（最多 8，前端也拦截）
- 邮箱未验证：`Link` 到 `/account`；未配 DeepSeek：`Link` 到 `/agent/settings`
- 未开启空态文案（与 spec 一致）：「勾选来源并开启后，新文章会出现在这里；只有可能影响股价的才发邮件。刚开启不会把旧闻刷进来。」
- 收件箱筛选全部/已发信/仅收录；卡片展示标题、来源、时间、方向、分数、摘要、板块、个股、原文 `<a target="_blank" rel="noreferrer">`、发信状态
- `useEffect` 每 10s 拉 settings+items；卸载 clearInterval
- 沿用 `section-title` / `meta-line` / `btn` / `status` / `muted`，不要新设计体系
- **不要**做「立即重扫」按钮

- [ ] **Step 1: Write the failing tests**

`TopbarNav.test.tsx` 的 Agent 用例增加：

```ts
expect(screen.getByRole('link', { name: '政策雷达' })).toHaveAttribute(
  'href',
  '/agent/policy-watch',
)
const jobsIdx = AGENT_NAV_LINKS.findIndex((l) => l.to === '/agent/jobs')
const radarIdx = AGENT_NAV_LINKS.findIndex((l) => l.to === '/agent/policy-watch')
expect(jobsIdx).toBeGreaterThanOrEqual(0)
expect(radarIdx).toBe(jobsIdx + 1)
```

`PolicyWatchPage.test.tsx`：mock `../api` 的四个函数。渲染后可见「政策雷达」、空态文案、灵敏度「中」。点开启后 `savePolicyWatchSettings` 被调用且 `enabled: true`。

- [ ] **Step 2: Run to see FAIL**

Run: `cd frontend-advisor && npx vitest run src/components/TopbarNav.test.tsx src/pages/PolicyWatchPage.test.tsx`  
Expected: FAIL（无链接/无页面）

- [ ] **Step 3: Implement nav、api、page、route、README 一行**

`App.tsx` import `PolicyWatchPage`，路由：

```tsx
<Route path="/agent/jobs" element={<MonitorJobsPage />} />
<Route path="/agent/policy-watch" element={<PolicyWatchPage />} />
```

- [ ] **Step 4: Run tests**

Run: `cd frontend-advisor && npx vitest run src/components/TopbarNav.test.tsx src/pages/PolicyWatchPage.test.tsx`  
Expected: PASS

再跑：`cd backend && .venv/bin/python -m pytest tests/test_policy_watch_helpers.py tests/test_policy_watch_settings.py tests/test_policy_watch_store.py tests/test_policy_watch_discover.py tests/test_policy_watch_interpret.py tests/test_policy_watch_fanout.py tests/test_policy_watch_tick.py tests/test_policy_watch_routes.py tests/test_monitor_engine.py -v`

- [ ] **Step 5: Commit（默认跳过）**

```bash
git add frontend-advisor/src/api.ts frontend-advisor/src/pages/PolicyWatchPage.tsx frontend-advisor/src/pages/PolicyWatchPage.test.tsx frontend-advisor/src/components/TopbarNav.tsx frontend-advisor/src/components/TopbarNav.test.tsx frontend-advisor/src/App.tsx frontend-advisor/src/styles.css README.md
git commit -m "feat: add policy radar page for sources and inbox"
```

---

## Self-review（对照 spec）

| Spec 段落 | 任务 |
|-----------|------|
| 挂 monitor-worker、独立集合 | 3, 7 |
| 四预置源 + 自定义 8 条 + SSRF | 1, 2, 4, 8 |
| 抽链 / 种子不回放 / 共享 URL | 3, 4 |
| 灵敏度三档 + 邮件格式 + 同 tick 汇总 + 不重发洪水 | 1, 6 |
| 扫描时段与两档间隔、周末补扇出 | 1, 6 |
| DeepSeek / 邮箱降级 | 2, 5, 6, 9 |
| API + `/agent/policy-watch` 页 + 10s 轮询 | 8, 9 |
| 无对话工具、无立即重扫、不挡盯盘 | 7, 9（刻意不做） |
| 索引与 yaml | 1, 3 |

无 TBD。函数名在后续任务中与 Task 1–3 的 Produces 一致。
