# 首页 Agent 解读阶段进度 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 刷新首页 Agent 解读时通过 `progress.phase/message` 展示阶段文案。

**Architecture:** 后台在 news→brief→picks 切换时 `_save_brief`/`_set_progress` 写入 Mongo；`_public` 透出；前端轮询展示 `progress.message`。

**Tech Stack:** FastAPI + pytest；React + Vitest

## Global Constraints

- Spec: `docs/superpowers/specs/2026-08-03-home-news-brief-progress-design.md`
- 仅阶段文案；无 SSE / 百分比 / 工具名
- `ready`/`idle` 时 `progress=null`
- 文案固定：整理今日资讯… / 撰写市场解读… / 筛选资讯驱动观察股…

## File map

| File | Role |
|------|------|
| Modify `backend/app/advisor/home_news_brief.py` | progress 字段、阶段写入 |
| Modify `backend/tests/test_home_news_brief.py` | 阶段单测 |
| Modify `frontend-advisor/src/api.ts` | 类型 |
| Modify `frontend-advisor/src/pages/HomeNewsSection.tsx` | 展示 |
| Modify `frontend-advisor/src/pages/HomeNewsSection.test.tsx` | UI 单测 |

---

### Task 1: Backend progress

**Files:**
- Modify: `backend/app/advisor/home_news_brief.py`
- Modify: `backend/tests/test_home_news_brief.py`

**Interfaces:**
- `PROGRESS_PHASES = {"news": "整理今日资讯…", "brief": "撰写市场解读…", "picks": "筛选资讯驱动观察股…"}`
- `_set_progress(user_id, day, phase: str) -> None` 写入 `{phase, message}`，保留 status=running 及其它字段
- `_public` 增加 `progress`
- `_idle` / ready 保存时 `progress=None`
- `_spawn_refresh_thread`：news → brief → picks 前分别 `_set_progress`；`generate_home_news_brief` 内或线程内拆点均可，优先在 `_run` 与 `generate_home_news_brief` 内清晰切分

- [ ] **Step 1: Failing test** — `start_home_news_brief_refresh` mock spawn 后文档 progress 为 news；或测 `_set_progress` + public

```python
def test_public_includes_progress(monkeypatch):
    from app.advisor import home_news_brief as hb
    monkeypatch.setattr(hb, "last_trading_day", lambda: "2026-08-01")
    monkeypatch.setattr(
        hb,
        "_load_brief",
        lambda uid, day: {
            "trade_date": "2026-08-01",
            "status": "running",
            "summary": "",
            "bullets": [],
            "sectors": [],
            "symbols": [],
            "progress": {"phase": "picks", "message": "筛选资讯驱动观察股…"},
            "updated_at": "t",
            "error": None,
            "news_as_of": None,
        },
    )
    out = hb.get_home_news_brief("u1")
    assert out["progress"]["phase"] == "picks"
    assert "观察股" in out["progress"]["message"]


def test_set_progress_writes_phase(monkeypatch):
    from app.advisor import home_news_brief as hb
    saved = {}
    monkeypatch.setattr(hb, "last_trading_day", lambda: "2026-08-01")
    monkeypatch.setattr(
        hb,
        "_load_brief",
        lambda uid, day: {
            "status": "running",
            "summary": "old",
            "bullets": [],
            "sectors": [],
            "symbols": [],
        },
    )
    monkeypatch.setattr(
        hb,
        "_save_brief",
        lambda uid, day, fields: saved.update(fields) or {"status": "running", **fields, "trade_date": day},
    )
    hb._set_progress("u1", "2026-08-01", "brief")
    assert saved["progress"]["phase"] == "brief"
    assert saved["progress"]["message"] == "撰写市场解读…"
```

- [ ] **Step 2:** Run fail → implement → pass → commit `feat: expose home news brief progress phases`

实现要点：

```python
PROGRESS_MESSAGES = {
    "news": "整理今日资讯…",
    "brief": "撰写市场解读…",
    "picks": "筛选资讯驱动观察股…",
}

def _set_progress(user_id: str, day: str, phase: str) -> None:
    msg = PROGRESS_MESSAGES.get(phase)
    if not msg:
        return
    existing = _load_brief(user_id, day) or {}
    _save_brief(
        user_id,
        day,
        {
            "status": "running",
            "summary": existing.get("summary") or "",
            "bullets": existing.get("bullets") or [],
            "sectors": existing.get("sectors") or [],
            "symbols": existing.get("symbols") or [],
            "symbols_note": existing.get("symbols_note"),
            "error": None,
            "news_as_of": existing.get("news_as_of"),
            "progress": {"phase": phase, "message": msg},
        },
    )
```

在 `_run`：
```python
_set_progress(user_id, day, "news")
news = get_or_build_home_news(day)
_set_progress(user_id, day, "brief")
# 简报 LLM 段：可把 picks 切分放进 generate 或在 generate 前后：
parsed = generate_home_news_brief(user_id, news, on_before_picks=lambda: _set_progress(user_id, day, "picks"))
```
或更简单：改 `generate_home_news_brief` 增加可选 callback / 在函数内接受 `user_id, day` 并直接 `_set_progress`。推荐：`generate_home_news_brief` 在调 `run_home_news_stock_picks` 前调用 `_set_progress(user_id, day, "picks")`（需传入 day）。

ready 保存时显式 `"progress": None`。

---

### Task 2: Frontend

**Files:**
- Modify `frontend-advisor/src/api.ts` — `progress?: { phase: string; message: string } | null`
- Modify `HomeNewsSection.tsx` — running 展示 message
- Modify `HomeNewsSection.test.tsx`

- [ ] Test: mock brief `status:running, progress:{phase:'brief', message:'撰写市场解读…'}` → 可见该文案
- [ ] Implement display under button area
- [ ] Commit `feat: show home news brief progress message while running`

---

### Task 3: Verify

```bash
cd backend && .venv/bin/python -m pytest -q tests/test_home_news_brief.py
cd frontend-advisor && npm test -- --run src/pages/HomeNewsSection.test.tsx
```

---

## Self-review

Spec 阶段文案 / progress 字段 / ready 清空 / 前端展示 / 无 SSE → Task 1–2 覆盖。
