# 首页新闻热点与 Agent 解读 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在顾问市场首页驾驶舱下增加双栏「今日资讯 / Agent 解读」：共享新闻包默认同步拉取，个人解读默认读缓存，仅「刷新解读」才后台调 LLM。

**Architecture:** Mongo 存共享包 `home_news_daily`（按 `trade_date`）与用户简报 `home_news_briefs`（按 `user_id+trade_date`）。`GET /home/news` 冷启动聚合 `unstructured` + `list_hot_sectors`（不含重 LLM）；`POST /home/news-brief/refresh` 置 `running` 后线程生成 JSON 简报，可选 `web_research` 回写 `web` 组。前端 `HomeNewsSection` 独立加载，轮询简报直至非 `running`。

**Tech Stack:** FastAPI + Mongo + pytest（`backend`）；React + TypeScript + Vitest（`frontend-advisor`）；LLM 经 `build_chat_model` / `resolve_llm_credentials`。

## Global Constraints

- Spec: `docs/superpowers/specs/2026-08-02-home-news-brief-design.md`
- 布局：驾驶舱下整行双栏；窄屏先资讯后解读
- 默认读缓存；禁止首页自动 POST refresh / 自动烧 Token
- 资讯源：cctv / macro / index_sentiment / sectors / web（web 仅 refresh 且开启联网时写入）
- 缓存：共享新闻包 + 每人独立解读
- 单组失败不拖垮整页；文案中文；复用 `home-tile` / `meta-line`
- Docker 镜像标签规则与本功能无关

## File map

| File | Role |
|------|------|
| Create `backend/app/advisor/home_news.py` | 共享新闻包聚合、归一化 items、Mongo 读写 |
| Create `backend/app/advisor/home_news_brief.py` | 用户简报读写、LLM 生成、后台 refresh 线程、可选 web 回写 |
| Create `backend/tests/test_home_news.py` | 新闻包聚合与 GET 路由 |
| Create `backend/tests/test_home_news_brief.py` | brief 状态机、无 Key 拒绝、LLM mock |
| Modify `backend/app/advisor/routes.py` | 挂 3 个 `/home/news*` 路由 |
| Modify `frontend-advisor/src/api.ts` | 类型 + `fetchHomeNews` / `fetchHomeNewsBrief` / `refreshHomeNewsBrief` |
| Modify `frontend-advisor/src/api.home.test.ts` | API 路径单测 |
| Create `frontend-advisor/src/pages/HomeNewsSection.tsx` | 双栏 UI + 轮询 |
| Create `frontend-advisor/src/pages/HomeNewsSection.test.tsx` | 不自动 refresh、状态渲染 |
| Modify `frontend-advisor/src/pages/HomePage.tsx` | 驾驶舱下挂载 `HomeNewsSection` |
| Modify `frontend-advisor/src/pages/HomePage.test.tsx` | mock 新闻 API，断言模块存在且不自动 refresh |
| Modify `frontend-advisor/src/styles.css` | `.home-news-*` 双栏样式 |

---

### Task 1: 共享新闻包 `home_news` + GET `/home/news`

**Files:**
- Create: `backend/app/advisor/home_news.py`
- Create: `backend/tests/test_home_news.py`
- Modify: `backend/app/advisor/routes.py`（在 `/market/sectors` 附近新增路由）

**Interfaces:**
- Produces:
  - `get_or_build_home_news(trade_date: str | None = None, *, force: bool = False) -> dict`
  - Shape:
    ```python
    {
      "trade_date": "YYYY-MM-DD",
      "as_of": "ISO8601",
      "groups": {
        "cctv": {"ok": bool, "source": str | None, "error": str | None, "items": [NewsItem]},
        "macro": {...},
        "index_sentiment": {...},
        "sectors": {...},
        "web": {...},  # 冷启动恒为 ok=False items=[]（除非已有缓存含 web）
      },
    }
    ```
  - `NewsItem`: `{ "title": str, "summary": str | None, "published_at": str | None, "url": str | None, "tags": list[str] | None }`
  - Mongo: `get_db().home_news_daily`，unique index on `trade_date`
  - Route: `GET /api/advisor/home/news` → above（auth）

- [ ] **Step 1: Write failing tests**

```python
# backend/tests/test_home_news.py
from __future__ import annotations

from fastapi.testclient import TestClient


def test_build_home_news_partial_source_failure(monkeypatch):
    from app.advisor import home_news

    monkeypatch.setattr(home_news, "last_trading_day", lambda: "2026-08-01")
    monkeypatch.setattr(
        home_news,
        "_fetch_cctv_group",
        lambda day: {
            "ok": True,
            "source": "cctv",
            "error": None,
            "items": [{"title": "联播一条", "summary": None, "published_at": None, "url": None, "tags": None}],
        },
    )
    monkeypatch.setattr(
        home_news,
        "_fetch_macro_group",
        lambda day: {"ok": False, "source": "macro", "error": "down", "items": []},
    )
    monkeypatch.setattr(
        home_news,
        "_fetch_index_sentiment_group",
        lambda day: {"ok": False, "source": "idx", "error": "API 不可用", "items": []},
    )
    monkeypatch.setattr(
        home_news,
        "_fetch_sectors_group",
        lambda day: {
            "ok": True,
            "source": "sectors",
            "error": None,
            "items": [{"title": "人工智能", "summary": "+5.1%", "published_at": None, "url": None, "tags": ["sector"]}],
        },
    )
    monkeypatch.setattr(home_news, "_load_news_doc", lambda day: None)
    saved = {}

    def _save(doc):
        saved["doc"] = doc

    monkeypatch.setattr(home_news, "_save_news_doc", _save)

    out = home_news.get_or_build_home_news()
    assert out["trade_date"] == "2026-08-01"
    assert out["groups"]["cctv"]["ok"] is True
    assert out["groups"]["macro"]["ok"] is False
    assert out["groups"]["web"]["items"] == []
    assert saved["doc"]["groups"]["cctv"]["items"][0]["title"] == "联播一条"


def test_get_or_build_returns_cache(monkeypatch):
    from app.advisor import home_news

    cached = {
        "trade_date": "2026-08-01",
        "as_of": "2026-08-01T01:00:00+00:00",
        "groups": {
            "cctv": {"ok": True, "source": "c", "error": None, "items": []},
            "macro": {"ok": True, "source": "m", "error": None, "items": []},
            "index_sentiment": {"ok": False, "source": None, "error": "x", "items": []},
            "sectors": {"ok": True, "source": "s", "error": None, "items": []},
            "web": {"ok": False, "source": None, "error": None, "items": []},
        },
    }
    monkeypatch.setattr(home_news, "last_trading_day", lambda: "2026-08-01")
    monkeypatch.setattr(home_news, "_load_news_doc", lambda day: cached)
    called = {"n": 0}

    def boom(*a, **k):
        called["n"] += 1
        raise AssertionError("should not rebuild")

    monkeypatch.setattr(home_news, "_build_news_groups", boom)
    out = home_news.get_or_build_home_news()
    assert out["as_of"] == cached["as_of"]
    assert called["n"] == 0


def test_home_news_route(monkeypatch):
    from app.main import app
    from app.advisor import routes

    def _user():
        return {"id": "u1", "username": "t"}

    app.dependency_overrides[routes._user] = _user
    monkeypatch.setattr(
        routes,
        "get_or_build_home_news",
        lambda: {
            "trade_date": "2026-08-01",
            "as_of": "t",
            "groups": {
                "cctv": {"ok": True, "source": "c", "error": None, "items": []},
                "macro": {"ok": True, "source": "m", "error": None, "items": []},
                "index_sentiment": {"ok": False, "source": None, "error": "x", "items": []},
                "sectors": {"ok": True, "source": "s", "error": None, "items": []},
                "web": {"ok": False, "source": None, "error": None, "items": []},
            },
        },
    )
    try:
        r = TestClient(app).get("/api/advisor/home/news")
        assert r.status_code == 200
        assert r.json()["trade_date"] == "2026-08-01"
    finally:
        app.dependency_overrides.clear()


def test_home_news_requires_auth():
    from app.main import app

    assert TestClient(app).get("/api/advisor/home/news").status_code == 401
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && .venv/bin/python -m pytest -q tests/test_home_news.py`
Expected: FAIL（模块 / 路由不存在）

- [ ] **Step 3: Implement `home_news.py`**

```python
# backend/app/advisor/home_news.py
"""Shared daily news pack for advisor home."""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any

from ..db import get_db
from .calendar_util import last_trading_day
from .agent import unstructured as ustr
from .home_market import list_hot_sectors

_build_lock = threading.Lock()
GROUP_KEYS = ("cctv", "macro", "index_sentiment", "sectors", "web")


def _col():
    return get_db().home_news_daily


def _ensure_index() -> None:
    create = getattr(_col(), "create_index", None)
    if callable(create):
        create("trade_date", unique=True, name="trade_date_1")


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _item(
    title: str,
    *,
    summary: str | None = None,
    published_at: str | None = None,
    url: str | None = None,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    t = (title or "").strip()
    if not t:
        return {}
    return {
        "title": t[:200],
        "summary": (summary[:400] if summary else None),
        "published_at": published_at,
        "url": url,
        "tags": tags,
    }


def _empty_group(source: str | None = None, error: str | None = None) -> dict[str, Any]:
    return {"ok": False, "source": source, "error": error, "items": []}


def _fetch_cctv_group(day: str) -> dict[str, Any]:
    ymd = day.replace("-", "")[:8]
    try:
        raw = ustr.fetch_market_cctv_news(date=ymd, limit=10)
    except Exception as exc:
        return _empty_group("akshare.news_cctv", str(exc)[:300])
    items = []
    for row in raw.get("items") or []:
        if not isinstance(row, dict):
            continue
        title = str(row.get("title") or row.get("内容") or row.get("新闻") or "")
        it = _item(title, summary=str(row.get("summary") or row.get("内容") or "")[:200] or None)
        if it:
            items.append(it)
    err = raw.get("error")
    return {
        "ok": bool(items) and not err,
        "source": raw.get("source") or "akshare.news_cctv",
        "error": None if items else (str(err)[:300] if err else "empty"),
        "items": items,
    }


def _fetch_macro_group(day: str) -> dict[str, Any]:
    _ = day
    try:
        raw = ustr.fetch_macro_china_snapshot(limit=3)
    except Exception as exc:
        return _empty_group("akshare.macro_china_*", str(exc)[:300])
    items = []
    for block_name, block in (raw.get("blocks") or {}).items():
        for row in (block.get("items") or [])[-2:]:
            if not isinstance(row, dict):
                continue
            # 取首个非空字符串字段作标题
            vals = [str(v) for v in row.values() if v is not None and str(v).strip()]
            title = f"{block_name}: {' / '.join(vals[:3])}" if vals else ""
            it = _item(title, tags=["macro", str(block_name)])
            if it:
                items.append(it)
    return {
        "ok": bool(items),
        "source": raw.get("source") or "akshare.macro_china_*",
        "error": None if items else "empty",
        "items": items[:12],
    }


def _fetch_index_sentiment_group(day: str) -> dict[str, Any]:
    _ = day
    try:
        raw = ustr.fetch_index_news_sentiment(limit=12)
    except Exception as exc:
        return _empty_group("akshare.index_news_sentiment_scope", str(exc)[:300])
    items = []
    for row in raw.get("items") or []:
        if not isinstance(row, dict):
            continue
        title = str(row.get("title") or row.get("新闻标题") or row.get("name") or "")
        if not title:
            vals = [str(v) for v in row.values() if v is not None and str(v).strip()]
            title = " / ".join(vals[:3])
        it = _item(title, tags=["index_sentiment"])
        if it:
            items.append(it)
    err = raw.get("error")
    return {
        "ok": bool(items) and not err,
        "source": raw.get("source"),
        "error": None if items else (str(err)[:300] if err else "empty"),
        "items": items,
    }


def _fetch_sectors_group(day: str) -> dict[str, Any]:
    try:
        raw = list_hot_sectors(top=8, trade_date=day)
    except Exception as exc:
        return _empty_group("home_market.list_hot_sectors", str(exc)[:300])
    items = []
    for row in raw.get("items") or []:
        name = str(row.get("name") or "")
        pct = row.get("change_pct")
        summary = f"{pct:+.2f}%" if isinstance(pct, (int, float)) else None
        it = _item(name, summary=summary, tags=["sector"])
        if it:
            items.append(it)
    return {
        "ok": bool(items) and bool(raw.get("ok")),
        "source": raw.get("source") or "sectors",
        "error": None if items else (raw.get("error") or "empty"),
        "items": items,
    }


def _build_news_groups(day: str) -> dict[str, Any]:
    return {
        "cctv": _fetch_cctv_group(day),
        "macro": _fetch_macro_group(day),
        "index_sentiment": _fetch_index_sentiment_group(day),
        "sectors": _fetch_sectors_group(day),
        "web": _empty_group(None, None),  # filled on brief refresh when web_research on
    }


def _public(doc: dict[str, Any]) -> dict[str, Any]:
    groups = {}
    raw_groups = doc.get("groups") or {}
    for k in GROUP_KEYS:
        g = raw_groups.get(k) or _empty_group()
        groups[k] = {
            "ok": bool(g.get("ok")),
            "source": g.get("source"),
            "error": g.get("error"),
            "items": list(g.get("items") or []),
        }
    return {
        "trade_date": str(doc.get("trade_date") or "")[:10],
        "as_of": str(doc.get("as_of") or ""),
        "groups": groups,
    }


def _load_news_doc(day: str) -> dict[str, Any] | None:
    doc = _col().find_one({"trade_date": day}, {"_id": 0})
    return doc


def _save_news_doc(doc: dict[str, Any]) -> None:
    _ensure_index()
    day = str(doc["trade_date"])[:10]
    _col().update_one(
        {"trade_date": day},
        {"$set": {**doc, "trade_date": day}},
        upsert=True,
    )


def merge_web_group(trade_date: str, web_group: dict[str, Any]) -> dict[str, Any]:
    """Update only the web group on an existing pack (create empty shell if missing)."""
    day = (trade_date or last_trading_day())[:10]
    with _build_lock:
        doc = _load_news_doc(day)
        if not doc:
            doc = {
                "trade_date": day,
                "as_of": _iso_now(),
                "groups": _build_news_groups(day),
            }
        groups = dict(doc.get("groups") or {})
        groups["web"] = {
            "ok": bool(web_group.get("ok")),
            "source": web_group.get("source") or "web_research",
            "error": web_group.get("error"),
            "items": list(web_group.get("items") or [])[:12],
        }
        doc = {**doc, "groups": groups, "as_of": _iso_now(), "trade_date": day}
        _save_news_doc(doc)
        return _public(doc)


def get_or_build_home_news(
    trade_date: str | None = None, *, force: bool = False
) -> dict[str, Any]:
    day = (trade_date or last_trading_day())[:10]
    if not force:
        cached = _load_news_doc(day)
        if cached:
            return _public(cached)
    with _build_lock:
        if not force:
            cached = _load_news_doc(day)
            if cached:
                return _public(cached)
        groups = _build_news_groups(day)
        doc = {"trade_date": day, "as_of": _iso_now(), "groups": groups}
        _save_news_doc(doc)
        return _public(doc)
```

- [ ] **Step 4: Wire route**

在 `routes.py` 顶部 import 区增加（或路由内局部 import）`get_or_build_home_news`，并在 `/market/sectors` 后添加：

```python
@router.get("/home/news")
def home_news(user: dict[str, Any] = Depends(_user)) -> dict[str, Any]:
    from .home_news import get_or_build_home_news

    return get_or_build_home_news()
```

若测试里 `monkeypatch.setattr(routes, "get_or_build_home_news", ...)`，则改为模块级 import：

```python
from .home_news import get_or_build_home_news
```

并在路由中直接调用 `return get_or_build_home_news()`。

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && .venv/bin/python -m pytest -q tests/test_home_news.py`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/advisor/home_news.py backend/tests/test_home_news.py backend/app/advisor/routes.py
git commit -m "$(cat <<'EOF'
feat: add shared home news pack API

EOF
)"
```

---

### Task 2: 用户简报存储、LLM 生成与 refresh 状态机

**Files:**
- Create: `backend/app/advisor/home_news_brief.py`
- Create: `backend/tests/test_home_news_brief.py`
- Modify: `backend/app/advisor/routes.py`（GET brief + POST refresh）

**Interfaces:**
- Consumes: `get_or_build_home_news`, `merge_web_group`；`resolve_llm_credentials` / `build_chat_model`；`web_tool_flags` / `run_web_research`；可选 `knowledge.list_items`
- Produces:
  - `get_home_news_brief(user_id: str, trade_date: str | None = None) -> dict`
    - 无文档时：`{"trade_date", "status": "idle", "summary": "", "bullets": [], "sectors": [], "symbols": [], "updated_at": None, "error": None, "news_as_of": None}`
  - `start_home_news_brief_refresh(user_id: str, trade_date: str | None = None) -> dict`
    - 无 DeepSeek Key → raise `ValueError`（路由转 400，detail 含「Agent 设置」）
    - 已 `running` 且线程活着 → 返回现文档
    - 否则 upsert `status=running` 并启动 daemon 线程
  - Brief shape:
    ```python
    {
      "trade_date": str,
      "status": "idle"|"running"|"ready"|"failed",
      "summary": str,
      "bullets": list[str],  # ≤5
      "sectors": [{"name": str, "reason": str}],
      "symbols": [{"symbol": str, "name": str, "reason": str}],
      "updated_at": str | None,
      "error": str | None,
      "news_as_of": str | None,
    }
    ```
  - Routes:
    - `GET /api/advisor/home/news-brief`
    - `POST /api/advisor/home/news-brief/refresh`

- [ ] **Step 1: Write failing tests**

```python
# backend/tests/test_home_news_brief.py
from __future__ import annotations

import json

from fastapi.testclient import TestClient


def test_get_brief_idle_when_missing(monkeypatch):
    from app.advisor import home_news_brief as hb

    monkeypatch.setattr(hb, "last_trading_day", lambda: "2026-08-01")
    monkeypatch.setattr(hb, "_load_brief", lambda uid, day: None)
    out = hb.get_home_news_brief("u1")
    assert out["status"] == "idle"
    assert out["bullets"] == []


def test_generate_brief_parses_llm_json(monkeypatch):
    from app.advisor import home_news_brief as hb

    class FakeModel:
        def invoke(self, messages):
            class R:
                content = json.dumps(
                    {
                        "summary": "政策偏暖，成长占优",
                        "bullets": ["联播提及科技创新", "流动性边际改善"],
                        "sectors": [{"name": "人工智能", "reason": "题材活跃"}],
                        "symbols": [{"symbol": "600519", "name": "贵州茅台", "reason": "示例"}],
                    },
                    ensure_ascii=False,
                )

            return R()

    monkeypatch.setattr(hb, "resolve_llm_credentials", lambda uid: {"api_key": "x"})
    monkeypatch.setattr(hb, "build_chat_model", lambda uid, **k: FakeModel())
    monkeypatch.setattr(hb, "_optional_knowledge_titles", lambda uid: [])
    monkeypatch.setattr(hb, "_maybe_fetch_web_items", lambda uid: [])

    news = {
        "trade_date": "2026-08-01",
        "as_of": "t0",
        "groups": {
            "cctv": {
                "ok": True,
                "source": "c",
                "error": None,
                "items": [{"title": "联播", "summary": None, "published_at": None, "url": None, "tags": None}],
            },
            "macro": {"ok": True, "source": "m", "error": None, "items": []},
            "index_sentiment": {"ok": False, "source": None, "error": "x", "items": []},
            "sectors": {
                "ok": True,
                "source": "s",
                "error": None,
                "items": [{"title": "人工智能", "summary": "+5%", "published_at": None, "url": None, "tags": ["sector"]}],
            },
            "web": {"ok": False, "source": None, "error": None, "items": []},
        },
    }
    out = hb.generate_home_news_brief("u1", news)
    assert out["summary"].startswith("政策")
    assert len(out["bullets"]) == 2
    assert out["sectors"][0]["name"] == "人工智能"
    assert out["symbols"][0]["symbol"] == "600519"


def test_refresh_rejects_without_llm_key(monkeypatch):
    from app.advisor import home_news_brief as hb

    monkeypatch.setattr(hb, "last_trading_day", lambda: "2026-08-01")

    def _boom(uid):
        raise ValueError("尚未配置 DeepSeek API Key，请先在 Agent 设置中填写")

    monkeypatch.setattr(hb, "resolve_llm_credentials", _boom)
    try:
        hb.start_home_news_brief_refresh("u1")
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "DeepSeek" in str(exc)


def test_refresh_reuses_running(monkeypatch):
    from app.advisor import home_news_brief as hb

    monkeypatch.setattr(hb, "last_trading_day", lambda: "2026-08-01")
    monkeypatch.setattr(hb, "resolve_llm_credentials", lambda uid: {"api_key": "x"})
    existing = {
        "user_id": "u1",
        "trade_date": "2026-08-01",
        "status": "running",
        "summary": "",
        "bullets": [],
        "sectors": [],
        "symbols": [],
        "updated_at": "t",
        "error": None,
        "news_as_of": None,
    }
    monkeypatch.setattr(hb, "_load_brief", lambda uid, day: existing)
    monkeypatch.setattr(hb, "_thread_alive_for", lambda uid, day: True)
    started = {"n": 0}
    monkeypatch.setattr(hb, "_spawn_refresh_thread", lambda *a, **k: started.__setitem__("n", started["n"] + 1))
    out = hb.start_home_news_brief_refresh("u1")
    assert out["status"] == "running"
    assert started["n"] == 0


def test_brief_routes(monkeypatch):
    from app.main import app
    from app.advisor import routes

    def _user():
        return {"id": "u1", "username": "t"}

    app.dependency_overrides[routes._user] = _user
    monkeypatch.setattr(
        routes,
        "get_home_news_brief",
        lambda uid: {
            "trade_date": "2026-08-01",
            "status": "idle",
            "summary": "",
            "bullets": [],
            "sectors": [],
            "symbols": [],
            "updated_at": None,
            "error": None,
            "news_as_of": None,
        },
    )

    def _refresh(uid):
        raise ValueError("尚未配置 DeepSeek API Key，请先在 Agent 设置中填写")

    monkeypatch.setattr(routes, "start_home_news_brief_refresh", _refresh)
    try:
        client = TestClient(app)
        g = client.get("/api/advisor/home/news-brief")
        assert g.status_code == 200
        assert g.json()["status"] == "idle"
        p = client.post("/api/advisor/home/news-brief/refresh")
        assert p.status_code == 400
        assert "DeepSeek" in p.json()["detail"]
    finally:
        app.dependency_overrides.clear()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && .venv/bin/python -m pytest -q tests/test_home_news_brief.py`
Expected: FAIL

- [ ] **Step 3: Implement `home_news_brief.py`**

```python
# backend/app/advisor/home_news_brief.py
"""Per-user home news Agent brief + refresh job."""

from __future__ import annotations

import json
import re
import threading
from datetime import datetime, timezone
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from ..db import get_db
from .agent.llm import build_chat_model
from .agent.web_research import run_web_research
from .calendar_util import last_trading_day
from .home_news import get_or_build_home_news, merge_web_group
from .llm_settings import resolve_llm_credentials, web_tool_flags

_lock = threading.Lock()
_threads: dict[str, threading.Thread] = {}  # key = f"{user_id}:{trade_date}"


def _col():
    return get_db().home_news_briefs


def _ensure_index() -> None:
    create = getattr(_col(), "create_index", None)
    if callable(create):
        create(
            [("user_id", 1), ("trade_date", 1)],
            unique=True,
            name="user_trade_date_1",
        )


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _thread_key(user_id: str, day: str) -> str:
    return f"{user_id}:{day}"


def _idle(day: str) -> dict[str, Any]:
    return {
        "trade_date": day,
        "status": "idle",
        "summary": "",
        "bullets": [],
        "sectors": [],
        "symbols": [],
        "updated_at": None,
        "error": None,
        "news_as_of": None,
    }


def _public(doc: dict[str, Any] | None, day: str) -> dict[str, Any]:
    if not doc:
        return _idle(day)
    return {
        "trade_date": str(doc.get("trade_date") or day)[:10],
        "status": str(doc.get("status") or "idle"),
        "summary": str(doc.get("summary") or ""),
        "bullets": [str(x) for x in (doc.get("bullets") or [])][:5],
        "sectors": [
            {"name": str(x.get("name") or ""), "reason": str(x.get("reason") or "")}
            for x in (doc.get("sectors") or [])
            if isinstance(x, dict) and x.get("name")
        ][:8],
        "symbols": [
            {
                "symbol": str(x.get("symbol") or ""),
                "name": str(x.get("name") or ""),
                "reason": str(x.get("reason") or ""),
            }
            for x in (doc.get("symbols") or [])
            if isinstance(x, dict) and re.fullmatch(r"\d{6}", str(x.get("symbol") or ""))
        ][:8],
        "updated_at": doc.get("updated_at"),
        "error": doc.get("error"),
        "news_as_of": doc.get("news_as_of"),
    }


def _load_brief(user_id: str, day: str) -> dict[str, Any] | None:
    return _col().find_one({"user_id": user_id, "trade_date": day}, {"_id": 0})


def _save_brief(user_id: str, day: str, fields: dict[str, Any]) -> dict[str, Any]:
    _ensure_index()
    payload = {
        **fields,
        "user_id": user_id,
        "trade_date": day,
        "updated_at": _iso_now(),
    }
    _col().update_one(
        {"user_id": user_id, "trade_date": day},
        {"$set": payload},
        upsert=True,
    )
    return _public(payload, day)


def get_home_news_brief(user_id: str, trade_date: str | None = None) -> dict[str, Any]:
    day = (trade_date or last_trading_day())[:10]
    return _public(_load_brief(user_id, day), day)


def _optional_knowledge_titles(user_id: str) -> list[str]:
    try:
        from .knowledge import list_items

        items = list_items(user_id, summary=True) or []
        out = []
        for it in items[:8]:
            t = str(it.get("title") or it.get("name") or "").strip()
            if t:
                out.append(t[:80])
        return out
    except Exception:
        return []


def _truncate_news_for_prompt(news: dict[str, Any]) -> dict[str, Any]:
    groups = {}
    for k, g in (news.get("groups") or {}).items():
        items = []
        for it in (g.get("items") or [])[:8]:
            if not isinstance(it, dict):
                continue
            items.append(
                {
                    "title": str(it.get("title") or "")[:120],
                    "summary": (str(it.get("summary") or "")[:160] or None),
                }
            )
        groups[k] = {"ok": bool(g.get("ok")), "items": items}
    return {"trade_date": news.get("trade_date"), "groups": groups}


def _parse_llm_json(text: str) -> dict[str, Any]:
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:].strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            data = json.loads(text[start : end + 1])
        else:
            data = {"summary": text[:200], "bullets": [], "sectors": [], "symbols": []}
    if not isinstance(data, dict):
        data = {}
    bullets = [str(x)[:120] for x in (data.get("bullets") or []) if str(x).strip()][:5]
    sectors = []
    for x in data.get("sectors") or []:
        if not isinstance(x, dict):
            continue
        name = str(x.get("name") or "").strip()
        if name:
            sectors.append({"name": name[:40], "reason": str(x.get("reason") or "")[:80]})
    symbols = []
    for x in data.get("symbols") or []:
        if not isinstance(x, dict):
            continue
        sym = re.sub(r"\D", "", str(x.get("symbol") or ""))[-6:]
        if not re.fullmatch(r"\d{6}", sym):
            continue
        symbols.append(
            {
                "symbol": sym,
                "name": str(x.get("name") or "")[:40],
                "reason": str(x.get("reason") or "")[:80],
            }
        )
    return {
        "summary": str(data.get("summary") or "")[:200],
        "bullets": bullets,
        "sectors": sectors[:8],
        "symbols": symbols[:8],
    }


def _maybe_fetch_web_items(user_id: str) -> list[dict[str, Any]]:
    flags = web_tool_flags(user_id)
    if not flags.get("web_research"):
        return []
    try:
        creds = resolve_llm_credentials(user_id)
        raw = run_web_research(
            creds["api_key"],
            "今日A股市场政策与舆情热点摘要（简体中文，列要点）",
        )
        text = str(raw or "").strip()
        if not text:
            return []
        # 按行切成要点
        items = []
        for line in text.splitlines():
            line = line.strip(" -*\t")
            if len(line) < 8:
                continue
            items.append(
                {
                    "title": line[:160],
                    "summary": None,
                    "published_at": None,
                    "url": None,
                    "tags": ["web"],
                }
            )
            if len(items) >= 8:
                break
        if not items:
            items = [
                {
                    "title": text[:160],
                    "summary": text[160:400] or None,
                    "published_at": None,
                    "url": None,
                    "tags": ["web"],
                }
            ]
        return items
    except Exception:
        return []


def generate_home_news_brief(user_id: str, news: dict[str, Any]) -> dict[str, Any]:
    resolve_llm_credentials(user_id)
    web_items = _maybe_fetch_web_items(user_id)
    if web_items:
        merge_web_group(
            str(news.get("trade_date") or ""),
            {"ok": True, "source": "web_research", "error": None, "items": web_items},
        )
        # refresh local view for prompt
        groups = dict(news.get("groups") or {})
        groups["web"] = {"ok": True, "source": "web_research", "error": None, "items": web_items}
        news = {**news, "groups": groups}

    model = build_chat_model(user_id, temperature=0.2, streaming=False)
    prompt = {
        "news": _truncate_news_for_prompt(news),
        "knowledge_titles": _optional_knowledge_titles(user_id),
    }
    system = (
        "你是投研助手。根据今日资讯包，用中文输出市场解读 JSON（不要 Markdown 围栏）。"
        '格式: {"summary":"一句话","bullets":["..."],"sectors":[{"name":"...","reason":"..."}],'
        '"symbols":[{"symbol":"600000","name":"...","reason":"..."}]}。'
        "summary≤80字；bullets≤5条每条≤40字；sectors/symbols 各≤5；"
        "股票代码必须是6位A股；勿编造未提供的数据；勿给出保证收益或下单指令；"
        "表述为研究观察，非投资建议。"
    )
    resp = model.invoke(
        [
            SystemMessage(content=system),
            HumanMessage(content=json.dumps(prompt, ensure_ascii=False, default=str)),
        ]
    )
    text = resp.content if isinstance(resp.content, str) else str(resp.content)
    return _parse_llm_json(text)


def _thread_alive_for(user_id: str, day: str) -> bool:
    with _lock:
        th = _threads.get(_thread_key(user_id, day))
        return th is not None and th.is_alive()


def _spawn_refresh_thread(user_id: str, day: str) -> None:
    key = _thread_key(user_id, day)

    def _run() -> None:
        try:
            news = get_or_build_home_news(day)
            parsed = generate_home_news_brief(user_id, news)
            _save_brief(
                user_id,
                day,
                {
                    "status": "ready",
                    "summary": parsed["summary"],
                    "bullets": parsed["bullets"],
                    "sectors": parsed["sectors"],
                    "symbols": parsed["symbols"],
                    "error": None,
                    "news_as_of": news.get("as_of"),
                },
            )
        except Exception as exc:
            _save_brief(
                user_id,
                day,
                {
                    "status": "failed",
                    "error": f"{type(exc).__name__}: {exc}"[:400],
                },
            )
        finally:
            with _lock:
                _threads.pop(key, None)

    th = threading.Thread(target=_run, name=f"home-news-brief-{key}", daemon=True)
    with _lock:
        _threads[key] = th
    th.start()


def start_home_news_brief_refresh(
    user_id: str, trade_date: str | None = None
) -> dict[str, Any]:
    day = (trade_date or last_trading_day())[:10]
    resolve_llm_credentials(user_id)  # raise ValueError if missing

    existing = _load_brief(user_id, day)
    if existing and existing.get("status") == "running" and _thread_alive_for(user_id, day):
        return _public(existing, day)

    if existing and existing.get("status") == "running" and not _thread_alive_for(user_id, day):
        # 线程丢失：允许重新开跑
        pass

    out = _save_brief(
        user_id,
        day,
        {
            "status": "running",
            "summary": (existing or {}).get("summary") or "",
            "bullets": (existing or {}).get("bullets") or [],
            "sectors": (existing or {}).get("sectors") or [],
            "symbols": (existing or {}).get("symbols") or [],
            "error": None,
            "news_as_of": (existing or {}).get("news_as_of"),
        },
    )
    _spawn_refresh_thread(user_id, day)
    return out
```

- [ ] **Step 4: Wire routes**

```python
from .home_news_brief import get_home_news_brief, start_home_news_brief_refresh

@router.get("/home/news-brief")
def home_news_brief(user: dict[str, Any] = Depends(_user)) -> dict[str, Any]:
    uid = _bind(user)
    return get_home_news_brief(uid)


@router.post("/home/news-brief/refresh")
def home_news_brief_refresh(user: dict[str, Any] = Depends(_user)) -> dict[str, Any]:
    uid = _bind(user)
    try:
        return start_home_news_brief_refresh(uid)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
```

- [ ] **Step 5: Run tests**

Run: `cd backend && .venv/bin/python -m pytest -q tests/test_home_news.py tests/test_home_news_brief.py`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/advisor/home_news_brief.py backend/tests/test_home_news_brief.py backend/app/advisor/routes.py
git commit -m "$(cat <<'EOF'
feat: add per-user home news brief refresh job

EOF
)"
```

---

### Task 3: 前端 API 客户端

**Files:**
- Modify: `frontend-advisor/src/api.ts`（home 类型附近）
- Modify: `frontend-advisor/src/api.home.test.ts`

**Interfaces:**
- Produces:
  - `HomeNewsItem`, `HomeNewsGroup`, `HomeNewsResponse`
  - `HomeNewsBriefStatus = 'idle' | 'running' | 'ready' | 'failed'`
  - `HomeNewsBrief`
  - `fetchHomeNews()`, `fetchHomeNewsBrief()`, `refreshHomeNewsBrief()`

- [ ] **Step 1: Write failing API tests**

在 `api.home.test.ts` 追加（保留既有 mock 模式）：

```ts
it('fetchHomeNews hits /api/advisor/home/news', async () => {
  vi.mocked(auth.authFetch).mockResolvedValue({ trade_date: '2026-08-01', groups: {} })
  const { fetchHomeNews } = await import('./api')
  await fetchHomeNews()
  expect(auth.authFetch).toHaveBeenCalledWith('/api/advisor/home/news')
})

it('fetchHomeNewsBrief and refreshHomeNewsBrief hit brief endpoints', async () => {
  vi.mocked(auth.authFetch).mockResolvedValue({ status: 'idle' })
  const { fetchHomeNewsBrief, refreshHomeNewsBrief } = await import('./api')
  await fetchHomeNewsBrief()
  expect(auth.authFetch).toHaveBeenCalledWith('/api/advisor/home/news-brief')
  await refreshHomeNewsBrief()
  expect(auth.authFetch).toHaveBeenCalledWith('/api/advisor/home/news-brief/refresh', {
    method: 'POST',
  })
})
```

（若文件里 `authFetch` mock 写法不同，对齐现有 `api.home.test.ts` 的 import/vi.mock。）

- [ ] **Step 2: Run to verify fail**

Run: `cd frontend-advisor && npm test -- --run src/api.home.test.ts`
Expected: FAIL（函数未导出）

- [ ] **Step 3: Add API types + functions**

```ts
export type HomeNewsItem = {
  title: string
  summary?: string | null
  published_at?: string | null
  url?: string | null
  tags?: string[] | null
}

export type HomeNewsGroup = {
  ok: boolean
  source?: string | null
  error?: string | null
  items: HomeNewsItem[]
}

export type HomeNewsResponse = {
  trade_date: string
  as_of: string
  groups: {
    cctv: HomeNewsGroup
    macro: HomeNewsGroup
    index_sentiment: HomeNewsGroup
    sectors: HomeNewsGroup
    web: HomeNewsGroup
  }
}

export function fetchHomeNews(): Promise<HomeNewsResponse> {
  return authFetch('/api/advisor/home/news')
}

export type HomeNewsBriefStatus = 'idle' | 'running' | 'ready' | 'failed'

export type HomeNewsBrief = {
  trade_date: string
  status: HomeNewsBriefStatus
  summary: string
  bullets: string[]
  sectors: { name: string; reason: string }[]
  symbols: { symbol: string; name: string; reason: string }[]
  updated_at?: string | null
  error?: string | null
  news_as_of?: string | null
}

export function fetchHomeNewsBrief(): Promise<HomeNewsBrief> {
  return authFetch('/api/advisor/home/news-brief')
}

export function refreshHomeNewsBrief(): Promise<HomeNewsBrief> {
  return authFetch('/api/advisor/home/news-brief/refresh', { method: 'POST' })
}
```

- [ ] **Step 4: Run tests**

Run: `cd frontend-advisor && npm test -- --run src/api.home.test.ts`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend-advisor/src/api.ts frontend-advisor/src/api.home.test.ts
git commit -m "$(cat <<'EOF'
feat: add home news API client helpers

EOF
)"
```

---

### Task 4: `HomeNewsSection` UI + 挂到首页

**Files:**
- Create: `frontend-advisor/src/pages/HomeNewsSection.tsx`
- Create: `frontend-advisor/src/pages/HomeNewsSection.test.tsx`
- Modify: `frontend-advisor/src/pages/HomePage.tsx`
- Modify: `frontend-advisor/src/pages/HomePage.test.tsx`
- Modify: `frontend-advisor/src/styles.css`

**Interfaces:**
- Consumes: `fetchHomeNews` / `fetchHomeNewsBrief` / `refreshHomeNewsBrief`
- Produces: `<HomeNewsSection />`；挂在 `HomePage` 驾驶舱 `</div>`（`.home-grid`）之后
- 行为：挂载时只 GET news + GET brief；点击「刷新解读」才 POST；`running` 时每 2s 轮询 GET，最多约 90s

- [ ] **Step 1: Write failing component tests**

```tsx
// frontend-advisor/src/pages/HomeNewsSection.test.tsx
import '@testing-library/jest-dom/vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../api'
import { HomeNewsSection } from './HomeNewsSection'

vi.mock('../api', async () => {
  const actual = await vi.importActual<typeof import('../api')>('../api')
  return {
    ...actual,
    fetchHomeNews: vi.fn(),
    fetchHomeNewsBrief: vi.fn(),
    refreshHomeNewsBrief: vi.fn(),
  }
})

const emptyGroups = {
  cctv: {
    ok: true,
    source: 'c',
    error: null,
    items: [{ title: '联播头条', summary: null, published_at: null, url: null, tags: null }],
  },
  macro: { ok: false, source: null, error: 'x', items: [] },
  index_sentiment: { ok: false, source: null, error: 'x', items: [] },
  sectors: {
    ok: true,
    source: 's',
    error: null,
    items: [{ title: '人工智能', summary: '+5%', published_at: null, url: null, tags: ['sector'] }],
  },
  web: { ok: false, source: null, error: null, items: [] },
}

describe('HomeNewsSection', () => {
  beforeEach(() => {
    vi.mocked(api.fetchHomeNews).mockReset()
    vi.mocked(api.fetchHomeNewsBrief).mockReset()
    vi.mocked(api.refreshHomeNewsBrief).mockReset()
  })

  it('loads news and idle brief without calling refresh', async () => {
    vi.mocked(api.fetchHomeNews).mockResolvedValue({
      trade_date: '2026-08-01',
      as_of: 't',
      groups: emptyGroups,
    })
    vi.mocked(api.fetchHomeNewsBrief).mockResolvedValue({
      trade_date: '2026-08-01',
      status: 'idle',
      summary: '',
      bullets: [],
      sectors: [],
      symbols: [],
    })
    render(<HomeNewsSection />)
    await waitFor(() => expect(screen.getByText('联播头条')).toBeInTheDocument())
    expect(screen.getByText(/点「刷新解读」/)).toBeInTheDocument()
    expect(api.refreshHomeNewsBrief).not.toHaveBeenCalled()
  })

  it('refresh button posts and shows ready brief', async () => {
    vi.mocked(api.fetchHomeNews).mockResolvedValue({
      trade_date: '2026-08-01',
      as_of: 't',
      groups: emptyGroups,
    })
    vi.mocked(api.fetchHomeNewsBrief)
      .mockResolvedValueOnce({
        trade_date: '2026-08-01',
        status: 'idle',
        summary: '',
        bullets: [],
        sectors: [],
        symbols: [],
      })
      .mockResolvedValue({
        trade_date: '2026-08-01',
        status: 'ready',
        summary: '政策偏暖',
        bullets: ['要点一'],
        sectors: [{ name: '人工智能', reason: '活跃' }],
        symbols: [{ symbol: '600519', name: '贵州茅台', reason: '观察' }],
      })
    vi.mocked(api.refreshHomeNewsBrief).mockResolvedValue({
      trade_date: '2026-08-01',
      status: 'running',
      summary: '',
      bullets: [],
      sectors: [],
      symbols: [],
    })
    render(<HomeNewsSection />)
    await waitFor(() => expect(screen.getByRole('button', { name: '刷新解读' })).toBeEnabled())
    await userEvent.click(screen.getByRole('button', { name: '刷新解读' }))
    expect(api.refreshHomeNewsBrief).toHaveBeenCalledTimes(1)
    await waitFor(() => expect(screen.getByText('政策偏暖')).toBeInTheDocument())
    expect(screen.getByText('人工智能')).toBeInTheDocument()
    expect(screen.getByText(/600519/)).toBeInTheDocument()
  })
})
```

若项目未装 `@testing-library/user-event`，改用 `fireEvent.click`。

- [ ] **Step 2: Run to verify fail**

Run: `cd frontend-advisor && npm test -- --run src/pages/HomeNewsSection.test.tsx`
Expected: FAIL（组件不存在）

- [ ] **Step 3: Implement `HomeNewsSection.tsx`**

```tsx
import { useEffect, useRef, useState } from 'react'
import {
  fetchHomeNews,
  fetchHomeNewsBrief,
  refreshHomeNewsBrief,
  type HomeNewsBrief,
  type HomeNewsGroup,
  type HomeNewsResponse,
} from '../api'

const GROUP_LABELS: { key: keyof HomeNewsResponse['groups']; label: string }[] = [
  { key: 'cctv', label: '联播' },
  { key: 'macro', label: '宏观政策' },
  { key: 'index_sentiment', label: '指数情绪' },
  { key: 'sectors', label: '题材热点' },
  { key: 'web', label: '联网舆情' },
]

function visibleGroup(g: HomeNewsGroup | undefined): boolean {
  return Boolean(g && g.items && g.items.length > 0)
}

export function HomeNewsSection() {
  const [news, setNews] = useState<HomeNewsResponse | null>(null)
  const [newsError, setNewsError] = useState<string | null>(null)
  const [newsLoading, setNewsLoading] = useState(true)
  const [brief, setBrief] = useState<HomeNewsBrief | null>(null)
  const [briefError, setBriefError] = useState<string | null>(null)
  const pollRef = useRef<number | null>(null)

  const loadNews = () => {
    setNewsLoading(true)
    setNewsError(null)
    fetchHomeNews()
      .then((d) => setNews(d))
      .catch((e) => setNewsError(e instanceof Error ? e.message : String(e)))
      .finally(() => setNewsLoading(false))
  }

  const loadBrief = () => {
    fetchHomeNewsBrief()
      .then((d) => setBrief(d))
      .catch((e) => setBriefError(e instanceof Error ? e.message : String(e)))
  }

  useEffect(() => {
    loadNews()
    loadBrief()
    return () => {
      if (pollRef.current != null) window.clearInterval(pollRef.current)
    }
  }, [])

  useEffect(() => {
    if (brief?.status !== 'running') {
      if (pollRef.current != null) {
        window.clearInterval(pollRef.current)
        pollRef.current = null
      }
      return
    }
    let ticks = 0
    pollRef.current = window.setInterval(() => {
      ticks += 1
      if (ticks > 45) {
        if (pollRef.current != null) window.clearInterval(pollRef.current)
        pollRef.current = null
        setBriefError('解读生成超时，请稍后重试')
        return
      }
      fetchHomeNewsBrief()
        .then((d) => {
          setBrief(d)
          if (d.status === 'ready') loadNews() // web 组可能已更新
        })
        .catch(() => {})
    }, 2000)
    return () => {
      if (pollRef.current != null) window.clearInterval(pollRef.current)
    }
  }, [brief?.status])

  const onRefresh = async () => {
    setBriefError(null)
    try {
      const d = await refreshHomeNewsBrief()
      setBrief(d)
    } catch (e) {
      setBriefError(e instanceof Error ? e.message : String(e))
    }
  }

  const status = brief?.status || 'idle'
  const refreshing = status === 'running'

  return (
    <section className="home-news" aria-label="今日资讯与解读">
      <div className="home-news-grid">
        <div className="home-tile home-news-pane">
          <div className="home-news-pane-head">
            <h3 className="home-tile-title">今日资讯</h3>
            {news?.as_of ? <span className="meta-line">更新 {news.as_of}</span> : null}
          </div>
          {newsLoading ? <div className="home-tile-skeleton" /> : null}
          {newsError ? (
            <div className="home-tile-error">
              <p>{newsError}</p>
              <button type="button" className="btn ghost" onClick={loadNews}>
                重试
              </button>
            </div>
          ) : null}
          {!newsLoading && news
            ? GROUP_LABELS.filter(({ key }) => visibleGroup(news.groups[key])).map(({ key, label }) => (
                <div key={key} className="home-news-group">
                  <h4>{label}</h4>
                  <ul className="home-news-list">
                    {news.groups[key].items.slice(0, 6).map((it, i) => (
                      <li key={`${key}-${i}`}>
                        <span>{it.title}</span>
                        {it.summary ? <span className="muted">{it.summary}</span> : null}
                      </li>
                    ))}
                  </ul>
                </div>
              ))
            : null}
          {!newsLoading && news && !GROUP_LABELS.some(({ key }) => visibleGroup(news.groups[key])) ? (
            <p className="muted">暂无资讯</p>
          ) : null}
        </div>

        <div className="home-tile home-news-pane">
          <div className="home-news-pane-head">
            <h3 className="home-tile-title">Agent 解读</h3>
            <button
              type="button"
              className="btn ghost"
              disabled={refreshing}
              onClick={onRefresh}
            >
              {refreshing ? '生成中…' : '刷新解读'}
            </button>
          </div>
          {briefError ? <p className="home-tile-error">{briefError}</p> : null}
          {status === 'idle' || !brief ? (
            <p className="muted">暂无解读。点「刷新解读」生成今日要点与相关板块/股票（会消耗 Token）。</p>
          ) : null}
          {status === 'running' ? <p className="muted">正在生成解读…</p> : null}
          {status === 'failed' ? (
            <p className="home-tile-error">{brief?.error || '生成失败'}</p>
          ) : null}
          {status === 'ready' && brief ? (
            <div className="home-news-brief">
              <p className="home-news-summary">{brief.summary}</p>
              {brief.bullets.length ? (
                <ul className="home-news-bullets">
                  {brief.bullets.map((b, i) => (
                    <li key={i}>{b}</li>
                  ))}
                </ul>
              ) : null}
              {brief.sectors.length ? (
                <div className="home-news-chips">
                  {brief.sectors.map((s) => (
                    <span key={s.name} className="home-news-chip" title={s.reason}>
                      {s.name}
                    </span>
                  ))}
                </div>
              ) : null}
              {brief.symbols.length ? (
                <ul className="home-news-symbols">
                  {brief.symbols.map((s) => (
                    <li key={s.symbol}>
                      <span className="mono">
                        {s.symbol} {s.name}
                      </span>
                      <span className="muted">{s.reason}</span>
                    </li>
                  ))}
                </ul>
              ) : null}
            </div>
          ) : null}
        </div>
      </div>
    </section>
  )
}
```

- [ ] **Step 4: Styles**

在 `styles.css` 的 `.home-grid` 段落后追加：

```css
.home-news {
  margin-top: 14px;
}

.home-news-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}

@media (max-width: 899px) {
  .home-news-grid {
    grid-template-columns: 1fr;
  }
}

.home-news-pane-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 0.4rem;
}

.home-news-group h4 {
  margin: 0.6rem 0 0.3rem;
  font-size: 0.85rem;
}

.home-news-list,
.home-news-bullets,
.home-news-symbols {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: 0.35rem;
}

.home-news-list li,
.home-news-symbols li {
  display: grid;
  gap: 0.15rem;
  font-size: 0.9rem;
}

.home-news-summary {
  margin: 0 0 0.5rem;
  font-weight: 600;
}

.home-news-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin: 0.5rem 0;
}

.home-news-chip {
  border: 1px solid var(--line);
  border-radius: 6px;
  padding: 2px 8px;
  font-size: 0.8rem;
  background: var(--bg2);
}
```

- [ ] **Step 5: Mount in HomePage**

```tsx
import { HomeNewsSection } from './HomeNewsSection'
// ...
// 在 return 的 </div>（home-grid）之后、</section> 之前：
<HomeNewsSection />
```

更新 `HomePage.test.tsx`：mock `fetchHomeNews` / `fetchHomeNewsBrief`，默认返回空组 + idle；在「renders market tiles…」等用例中确保不调用 `refreshHomeNewsBrief`；可加一条断言 `今日资讯` 出现。

- [ ] **Step 6: Run frontend tests**

Run:

```bash
cd frontend-advisor && npm test -- --run src/pages/HomeNewsSection.test.tsx src/pages/HomePage.test.tsx src/api.home.test.ts
```

Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add frontend-advisor/src/pages/HomeNewsSection.tsx \
  frontend-advisor/src/pages/HomeNewsSection.test.tsx \
  frontend-advisor/src/pages/HomePage.tsx \
  frontend-advisor/src/pages/HomePage.test.tsx \
  frontend-advisor/src/styles.css
git commit -m "$(cat <<'EOF'
feat: show home news dual-pane with on-demand agent brief

EOF
)"
```

---

### Task 5: 端到端冒烟（手工）+ 收尾核对

**Files:** 无新增代码（除非冒烟发现缺口）

- [ ] **Step 1: 启动本地 API + advisor UI**（沿用仓库既有方式）
- [ ] **Step 2: 打开 `/`，确认驾驶舱下有双栏；左栏有分组（或空态）；右栏 idle，网络面板无 POST `/news-brief/refresh`**
- [ ] **Step 3: 配置 DeepSeek 后点「刷新解读」，按钮变「生成中…」，最终出现 summary / 板块 / 股票**
- [ ] **Step 4: 未配置 Key 时刷新应看到明确错误（含设置提示）**
- [ ] **Step 5: 跑全量相关测试**

```bash
cd backend && .venv/bin/python -m pytest -q tests/test_home_news.py tests/test_home_news_brief.py
cd frontend-advisor && npm test -- --run src/api.home.test.ts src/pages/HomeNewsSection.test.tsx src/pages/HomePage.test.tsx
```

Expected: 全 PASS

- [ ] **Step 6: 若有小修，单独 commit；否则无需空 commit**

---

## Self-review (plan vs spec)

| Spec 要求 | Task |
|-----------|------|
| 驾驶舱下双栏、窄屏先资讯后解读 | Task 4 CSS + section |
| 默认读缓存 / 刷新才调 Agent | Task 2 + Task 4（无自动 POST） |
| 五类来源；web 仅 refresh+联网 | Task 1 冷启动；Task 2 `_maybe_fetch_web_items` |
| 共享新闻 + 个人 brief | Task 1 / 2 Mongo |
| GET news / GET brief / POST refresh | Task 1–2 routes |
| 失败态：单组隐藏、整包重试、idle/running/failed | Task 1 空组；Task 4 UI |
| 后端/前端测试清单 | Task 1–4 |
| 非目标（不做 SSE / 不做全站共享解读等） | 计划未引入 |

Placeholder scan: 无 TBD；关键函数均有签名与示例代码。  
类型一致性：`status` 枚举、`groups` 五键、`sectors/symbols` 字段前后一致。
