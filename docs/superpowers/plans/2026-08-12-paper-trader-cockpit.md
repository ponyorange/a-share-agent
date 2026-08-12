# Paper Trader Cockpit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 落地 `/paper/trader` 驾驶舱：聚合 cockpit API、上控下舱 UI、轮询、lightweight-charts 日 K。

**Architecture:** 后端 `build_cockpit(user_id)` 聚合 session/paper/candidates/decisions；前端新页轮询 cockpit，启停走现有 paper-trader 路由；选中标的用 `/api/kline?range=daily` + `lightweight-charts`。

**Tech Stack:** FastAPI、pytest、React、Vitest、lightweight-charts、现有 `authFetch`

## Global Constraints

- Spec：`docs/superpowers/specs/2026-08-12-paper-trader-cockpit-design.md`
- 版式固定「上控下舱」；路由 `/paper/trader`
- 不改 worker 自动下单语义；cockpit 只读聚合 + 配置 API 复用一期
- K 线：`GET /api/kline?symbol=&range=daily`（legacy → Akshare）
- 计划中的 commit 步骤默认跳过，除非用户明确要求提交
- Docker 镜像标签仍为 `名称:架构`

---

### File map

| 文件 | 职责 |
|------|------|
| `backend/app/advisor/paper_trader/cockpit.py` | `build_cockpit(user_id) -> dict` |
| `backend/app/advisor/routes.py` | `GET /paper-trader/cockpit` |
| `backend/tests/test_paper_trader_cockpit.py` | 聚合接口单测 |
| `frontend-advisor/package.json` | 加 `lightweight-charts` |
| `frontend-advisor/src/api.ts` | cockpit/启停/kline 客户端 |
| `frontend-advisor/src/pages/PaperTraderPage.tsx` | 驾驶舱页 |
| `frontend-advisor/src/components/PaperTraderChart.tsx` | K 线组件 |
| `frontend-advisor/src/pages/PaperTraderPage.test.tsx` | 路由/按钮测 |
| `frontend-advisor/src/App.tsx` | 路由 |
| `frontend-advisor/src/components/TopbarNav.tsx` | 导航「交易员」 |
| `frontend-advisor/src/pages/PaperPage.tsx` | 顶链到驾驶舱 |
| `frontend-advisor/src/App.test.tsx` / `TopbarNav.test.tsx` | 导航断言更新 |

---

### Task 1: cockpit 聚合 API

**Files:**
- Create: `backend/app/advisor/paper_trader/cockpit.py`
- Modify: `backend/app/advisor/routes.py`（在现有 paper-trader 路由旁加 GET cockpit）
- Create: `backend/tests/test_paper_trader_cockpit.py`

**Interfaces:**
- Produces: `build_cockpit(user_id: str, *, decisions_page=1, decisions_page_size=20) -> dict`
- Consumes: `get_session`, `list_decisions`, `build_candidates`, `get_account`, `trading_session`

- [ ] **Step 1: 写失败单测**

```python
# backend/tests/test_paper_trader_cockpit.py
def test_cockpit_stopped_without_session(monkeypatch):
    import app.advisor.paper_trader.cockpit as cp
    monkeypatch.setattr(cp, "get_session", lambda uid: None)
    monkeypatch.setattr(cp, "get_account", lambda uid, **k: {
        "cash": 100000, "equity": 100000, "market_value": 0, "positions": []
    })
    monkeypatch.setattr(cp, "build_candidates", lambda uid, limit=None: [])
    monkeypatch.setattr(cp, "list_decisions", lambda uid, **k: {
        "page": 1, "page_size": 20, "total": 0, "items": []
    })
    monkeypatch.setattr(cp, "trading_session", lambda: {
        "is_trading": False, "is_trading_day": True
    })
    out = cp.build_cockpit("u1")
    assert out["session"]["status"] == "stopped"
    assert "candidates" in out and "decisions" in out
    assert "meta" in out


def test_cockpit_candidate_error_isolated(monkeypatch):
    import app.advisor.paper_trader.cockpit as cp
    monkeypatch.setattr(cp, "get_session", lambda uid: {"status": "running", "id": "s1"})
    monkeypatch.setattr(cp, "get_account", lambda uid, **k: {
        "cash": 1, "equity": 1, "market_value": 0, "positions": []
    })
    def boom(*a, **k):
        raise RuntimeError("candidates down")
    monkeypatch.setattr(cp, "build_candidates", boom)
    monkeypatch.setattr(cp, "list_decisions", lambda uid, **k: {
        "page": 1, "page_size": 20, "total": 0, "items": []
    })
    monkeypatch.setattr(cp, "trading_session", lambda: {"is_trading": True, "is_trading_day": True})
    out = cp.build_cockpit("u1")
    assert out["session"]["status"] == "running"
    assert out["candidates"] == []
    assert "candidates" in (out.get("errors") or {})
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && .venv/bin/python -m pytest tests/test_paper_trader_cockpit.py -v`  
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现 cockpit.py + 路由**

`build_cockpit`：
- session = `get_session` or `{"status": "stopped"}`
- paper：截断 positions 至 20；`positions_count` 用全量 len
- candidates：try/except → errors
- decisions：try/except → errors
- meta：`trading_session()` + `server_now` ISO UTC

路由：

```python
@router.get("/paper-trader/cockpit")
def paper_trader_cockpit(user=Depends(_user)):
    from .paper_trader.cockpit import build_cockpit
    return build_cockpit(user["id"])
```

- [ ] **Step 4: 跑通单测**

Run: `cd backend && .venv/bin/python -m pytest tests/test_paper_trader_cockpit.py -v`  
Expected: PASS

- [ ] **Step 5: Commit（默认跳过）**

---

### Task 2: 前端 API + 导航/路由壳

**Files:**
- Modify: `frontend-advisor/src/api.ts`
- Modify: `frontend-advisor/src/App.tsx`
- Modify: `frontend-advisor/src/components/TopbarNav.tsx`
- Modify: `frontend-advisor/src/pages/PaperPage.tsx`（顶链）
- Modify: `frontend-advisor/src/App.test.tsx`、`TopbarNav.test.tsx`（若有「模拟盘」断言则补「交易员」）
- Create: `frontend-advisor/src/pages/PaperTraderPage.tsx`（最小占位：标题 + 拉 cockpit 状态）

**Interfaces:**
- Produces:
  - `fetchPaperTraderCockpit()`
  - `startPaperTrader(body?)` / `pausePaperTrader` / `stopPaperTrader` / `resumePaperTrader` / `patchPaperTrader`
  - `fetchAdvisorKline(symbol, range='daily')` → 调 `/api/kline?...`（注意：顾问前端 `authFetch` 基址若只代理 `/api/advisor`，K 线走相对 `/api/kline` 或现有 explorer 同源；实现时对照 `vite.config` proxy）

- [ ] **Step 1: 查 vite proxy**

Run: `grep -n proxy frontend-advisor/vite.config.ts frontend-advisor/vite.config.* 2>/dev/null; head -80 frontend-advisor/vite.config.ts`  
记下 `/api` 是否转发到后端 8000。K 线客户端必须打到可达路径。

- [ ] **Step 2: 写/更新导航测试（失败则改）**

在 `TopbarNav.test.tsx` 或 `App.test.tsx` 增加：交易员链接 `href="/paper/trader"`。

- [ ] **Step 3: 实现 api + 路由 + 占位页**

占位页：显示 `session.status` 与「加载中/错误」；导出 default。

PaperPage 顶部：

```tsx
<Link to="/paper/trader">打开交易员驾驶舱</Link>
```

- [ ] **Step 4: 跑前端相关测**

Run: `cd frontend-advisor && npm test -- --run src/App.test.tsx src/components/TopbarNav.test.tsx`  
Expected: PASS

- [ ] **Step 5: Commit（默认跳过）**

---

### Task 3: 顶栏启停 + 轮询

**Files:**
- Modify: `frontend-advisor/src/pages/PaperTraderPage.tsx`
- Create/Modify: `frontend-advisor/src/pages/PaperTraderPage.test.tsx`

- [ ] **Step 1: 写 RTL 测：点击启动调用 start API**

用 `vi.spyOn` / mock `api` 模块；render 页面；click 启动；assert mock 被调用。

- [ ] **Step 2: 实现顶栏**

- 状态徽章：stopped/running/paused/halted
- 按钮组随状态切换（对齐 spec §3）
- `useEffect` 轮询：`is_trading ? 20000 : 60000`；unmount clearInterval
- 操作成功后 `await refresh()`

- [ ] **Step 3: 跑测**

Run: `cd frontend-advisor && npm test -- --run src/pages/PaperTraderPage.test.tsx`  
Expected: PASS

- [ ] **Step 4: Commit（默认跳过）**

---

### Task 4: 候选 / 持仓 / 决策列表

**Files:**
- Modify: `PaperTraderPage.tsx`（或拆 `PaperTraderCandidates.tsx` / `PaperTraderDecisions.tsx` 若文件过长）
- 样式：复用现有 advisor 表格/卡片 class，避免新设计体系

- [ ] **Step 1: 实现左栏候选表 + 迷你持仓**

点击行 `setSelectedSymbol(symbol)`。方向色标：buy/sell/neutral。

- [ ] **Step 2: 实现右栏决策时间线**

列表展示时间、skip_reason、成交数、拦截数；展开显示 JSON 友好字段（llm_actions 等）。

- [ ] **Step 3: 手动/RTL：cockpit mock 数据渲染出行**

- [ ] **Step 4: Commit（默认跳过）**

---

### Task 5: lightweight-charts 日 K

**Files:**
- Modify: `frontend-advisor/package.json`（`npm install lightweight-charts`）
- Create: `frontend-advisor/src/components/PaperTraderChart.tsx`
- Modify: `PaperTraderPage.tsx` 嵌入图表
- Create: `frontend-advisor/src/components/PaperTraderChart.test.tsx`（mock fetch；断言请求 symbol）

- [ ] **Step 1: 安装依赖**

Run: `cd frontend-advisor && npm install lightweight-charts`

- [ ] **Step 2: 实现 PaperTraderChart**

Props: `symbol: string | null`  
- null → 占位「选择标的」  
- 有 symbol → `fetch /api/kline?symbol=&range=daily`，把 bars 映射为 `{ time, open, high, low, close }`（注意 API 字段名：实现时打印一条真实响应或读 `kline` provider 返回键，常见 `date`/`open`/`high`/`low`/`close`）  
- `useEffect` 创建 chart，cleanup `chart.remove()`  
- 附链接 `explorerKlineUrl(symbol)` 新窗口

- [ ] **Step 3: 跑测 + `npm run build` 类型检查**

Run:

```bash
cd frontend-advisor && npm test -- --run src/components/PaperTraderChart.test.tsx
cd frontend-advisor && npm run build
```

Expected: PASS / build OK

- [ ] **Step 4: Commit（默认跳过）**

---

### Task 6: 风控编辑 + halted 恢复 + 移动布局

**Files:**
- Modify: `PaperTraderPage.tsx`
- CSS：优先用现有 layout utility / 简单 grid；`@media` 单列

- [ ] **Step 1: 风控可展开表单**

字段绑定 session.risk + mode + interval_sec → PATCH；成功提示「下轮生效」。

- [ ] **Step 2: halted 恢复**

`window.confirm` 或小组件确认后 `resumePaperTrader({ confirm_halt_resume: true })`。

- [ ] **Step 3: 移动单列顺序**

控制 → 候选 → 图 → 决策 → 持仓。

- [ ] **Step 4: 全量相关回归**

```bash
cd backend && .venv/bin/python -m pytest tests/test_paper_trader_*.py tests/test_monitor_engine.py -v
cd frontend-advisor && npm test -- --run
```

Expected: PASS

- [ ] **Step 5: Commit（默认跳过）**

---

## Spec coverage

| Spec 项 | Task |
|---------|------|
| cockpit API | 1 |
| 导航/路由/paper 短链 | 2 |
| 启停+轮询 | 3 |
| 候选/持仓/决策 | 4 |
| lightweight-charts 日 K | 5 |
| 风控编辑/halted/移动 | 6 |

## Self-review

- 无 TBD；K 线字段名在 Task 5 要求对照真实 `/api/kline` 响应（实现步骤内读取，不猜）
- 启停 API 路径与一期一致：`/api/advisor/paper-trader/*`
