# Stock Watchlist Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为顾问前端增加按用户持久化的股票收藏：今日关注/诊断星标、带行情的「我的收藏」页，以及 Agent 查看/收藏/取消工具。

**Architecture:** 独立 Mongo 集合 `watchlists` + `watchlist.py`（镜像 `portfolio.py`）；HTTP `/api/advisor/watchlist*`；前端 `StarToggle` + `WatchlistPage`；Agent 在 `build_tools` 挂载无 confirm 写工具。

**Tech Stack:** FastAPI、MongoDB、pytest、React、Vitest、现有 `get_last_quote` / `trading_session`

## Global Constraints

- 集合名 `watchlists`；`user_id` 唯一索引
- 每用户最多 **100** 只；超限 HTTP 400 / 工具返回明确错误
- 增删幂等（重复收藏、取消不存在均成功）
- Agent 写操作 **不需要** `confirm=true`
- 收藏页带现价/涨跌幅；刷新策略对齐持仓一览（交易约 3s，非交易进页一次）
- 星标：空心=未收藏，实心=已收藏
- 本版不做备注/分组/提醒
- 计划中的 commit 步骤默认跳过，除非用户明确要求提交

---

### File map

| 文件 | 职责 |
|------|------|
| `backend/app/advisor/watchlist.py` | 加载/增删/状态/marks |
| `backend/app/db.py` | `watchlists` 唯一索引 |
| `backend/app/advisor/routes.py` | HTTP 路由 |
| `backend/tests/test_watchlist.py` | 核心单测 |
| `frontend-advisor/src/api.ts` | 类型与 API |
| `frontend-advisor/src/components/StarToggle.tsx` | 星标按钮 |
| `frontend-advisor/src/components/StarToggle.test.tsx` | 星标单测 |
| `frontend-advisor/src/components/RecommendationCard.tsx` | 卡片星标 |
| `frontend-advisor/src/pages/RecommendationsPage.tsx` | 表格星标 + status 批量 |
| `frontend-advisor/src/components/AdviceCard.tsx` | 诊断星标 |
| `frontend-advisor/src/pages/AdvicePage.tsx` | 传入 starred / onToggle |
| `frontend-advisor/src/pages/WatchlistPage.tsx` | 收藏页 |
| `frontend-advisor/src/App.tsx` | 导航 + 路由 |
| `frontend-advisor/src/styles.css` | 星标 / 收藏页样式 |
| `backend/app/advisor/agent/tools.py` | 三个工具 |
| `backend/app/advisor/agent/graph.py` | SYSTEM_PROMPT 补充 |

---

### Task 1: 后端 watchlist 核心 + 单测

**Files:**
- Create: `backend/app/advisor/watchlist.py`
- Create: `backend/tests/test_watchlist.py`
- Modify: `backend/app/db.py`（在 `db.portfolios.create_index` 后加一行）

**Interfaces:**
- Produces:
  - `WATCHLIST_MAX = 100`
  - `load_watchlist(user_id: str | None = None) -> dict` → `{ "items": [...] }`
  - `add_symbol(user_id: str, symbol: str, name: str | None = None) -> dict`
  - `remove_symbol(user_id: str, symbol: str) -> dict`
  - `watchlist_status(user_id: str, symbols: list[str]) -> dict` → `{ "starred": {sym: bool} }`
  - `watchlist_marks(user_id: str) -> dict`（含 `session` / `items` 含 price 等）
- Consumes: `get_db`, `normalize_symbol`, `get_last_quote`, `trading_session`；名称查找可复用 `portfolio._lookup_name` / `_best_name` 或在本模块写等价私有函数

- [ ] **Step 1: 写失败单测**

```python
# backend/tests/test_watchlist.py
from __future__ import annotations

import pytest

from app.advisor import watchlist as wl


class _FakeColl:
    def __init__(self):
        self.doc = None

    def find_one(self, q):
        if self.doc and self.doc.get("user_id") == q.get("user_id"):
            return self.doc
        return None

    def update_one(self, q, update, upsert=False):
        body = dict(self.doc or {"user_id": q["user_id"], "items": []})
        body.update(update.get("$set") or {})
        self.doc = body


class _FakeDB:
    def __init__(self):
        self.watchlists = _FakeColl()


def test_add_remove_idempotent(monkeypatch):
    db = _FakeDB()
    monkeypatch.setattr(wl, "get_db", lambda: db)
    monkeypatch.setattr(wl, "_lookup_name", lambda symbol: f"N-{symbol}")

    out = wl.add_symbol("u1", "510300")
    assert len(out["items"]) == 1
    assert out["items"][0]["symbol"] == "510300"
    assert out["items"][0]["name"] == "N-510300"

    out2 = wl.add_symbol("u1", "510300")
    assert len(out2["items"]) == 1

    out3 = wl.remove_symbol("u1", "510300")
    assert out3["items"] == []
    out4 = wl.remove_symbol("u1", "510300")
    assert out4["items"] == []


def test_max_limit(monkeypatch):
    db = _FakeDB()
    monkeypatch.setattr(wl, "get_db", lambda: db)
    monkeypatch.setattr(wl, "_lookup_name", lambda symbol: symbol)
    # 直接塞满 100 只
    db.watchlists.doc = {
        "user_id": "u1",
        "items": [
            {"symbol": f"{i:06d}", "name": f"{i:06d}", "added_at": "t"}
            for i in range(100)
        ],
    }
    with pytest.raises(ValueError, match="100"):
        wl.add_symbol("u1", "510300")


def test_status_and_marks(monkeypatch):
    db = _FakeDB()
    monkeypatch.setattr(wl, "get_db", lambda: db)
    monkeypatch.setattr(wl, "_lookup_name", lambda symbol: "沪深300ETF")
    wl.add_symbol("u1", "510300")

    st = wl.watchlist_status("u1", ["510300", "159915"])
    assert st["starred"]["510300"] is True
    assert st["starred"]["159915"] is False

    monkeypatch.setattr(
        "app.quote.get_last_quote",
        lambda symbol: {
            "symbol": symbol,
            "name": "沪深300ETF",
            "price": 4.2,
            "pre_close": 4.0,
            "day_chg_pct": 0.05,
            "error": None,
        },
    )
    monkeypatch.setattr(
        "app.quote.trading_session",
        lambda: {"is_trading": False, "now": "2026-07-28T22:00:00+08:00"},
    )
    marks = wl.watchlist_marks("u1")
    assert marks["count"] == 1
    assert marks["items"][0]["price"] == 4.2
    assert marks["items"][0]["day_chg_pct"] == 0.05
```

- [ ] **Step 2: 跑测确认失败**

Run: `cd backend && .venv/bin/python -m pytest tests/test_watchlist.py -q --tb=line`  
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现 `watchlist.py` + 索引**

实现要点：

```python
WATCHLIST_MAX = 100

def load_watchlist(user_id: str | None = None) -> dict[str, Any]: ...
def add_symbol(user_id: str, symbol: str, name: str | None = None) -> dict[str, Any]:
    # normalize；已存在则返回；len>=100 且新代码 → raise ValueError("收藏已达上限 100 只")
    # name = name or _lookup_name(sym) or sym；added_at = datetime.now(timezone.utc)
def remove_symbol(user_id: str, symbol: str) -> dict[str, Any]: ...
def watchlist_status(user_id: str, symbols: list[str]) -> dict[str, Any]: ...
def watchlist_marks(user_id: str) -> dict[str, Any]:
    # 对每项 get_last_quote；名称用 quote 优先（忽略 name==symbol 占位）
```

`db.py` 增加：`db.watchlists.create_index("user_id", unique=True)`

- [ ] **Step 4: 跑测确认通过**

Run: `cd backend && .venv/bin/python -m pytest tests/test_watchlist.py -q --tb=short`  
Expected: PASS

- [ ] **Step 5: Commit**（仅当用户要求时）

```bash
git add backend/app/advisor/watchlist.py backend/tests/test_watchlist.py backend/app/db.py
git commit -m "feat: add watchlist storage and marks"
```

---

### Task 2: HTTP 路由

**Files:**
- Modify: `backend/app/advisor/routes.py`（紧接 `/portfolio*` 路由之后）

**Interfaces:**
- Consumes: Task 1 全部公开函数
- Produces:
  - `GET /watchlist` → `load_watchlist`
  - `GET /watchlist/marks` → `watchlist_marks`（异常 502）
  - `GET /watchlist/status?symbols=` → 逗号分隔，空则 `{starred:{}}`
  - `POST /watchlist/{symbol}` → `add_symbol`；`ValueError` → 400
  - `DELETE /watchlist/{symbol}` → `remove_symbol`

- [ ] **Step 1: 添加路由**

```python
@router.get("/watchlist")
def watchlist_get(user: dict[str, Any] = Depends(_user)) -> dict[str, Any]:
    _bind(user)
    from .watchlist import load_watchlist
    return load_watchlist(user["id"])


@router.get("/watchlist/marks")
def watchlist_marks_get(user: dict[str, Any] = Depends(_user)) -> dict[str, Any]:
    from .watchlist import watchlist_marks
    _bind(user)
    try:
        return watchlist_marks(user["id"])
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"收藏行情失败: {type(exc).__name__}"
        ) from exc


@router.get("/watchlist/status")
def watchlist_status_get(
    symbols: str = Query(default=""),
    user: dict[str, Any] = Depends(_user),
) -> dict[str, Any]:
    from .watchlist import watchlist_status
    _bind(user)
    parts = [s.strip() for s in symbols.split(",") if s.strip()]
    return watchlist_status(user["id"], parts)


@router.post("/watchlist/{symbol}")
def watchlist_add(
    symbol: str,
    name: str | None = Query(default=None),
    user: dict[str, Any] = Depends(_user),
) -> dict[str, Any]:
    from .watchlist import add_symbol
    _bind(user)
    try:
        return add_symbol(user["id"], symbol, name=name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/watchlist/{symbol}")
def watchlist_remove(
    symbol: str, user: dict[str, Any] = Depends(_user)
) -> dict[str, Any]:
    from .watchlist import remove_symbol
    _bind(user)
    try:
        return remove_symbol(user["id"], symbol)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
```

- [ ] **Step 2: 冒烟导入**

Run: `cd backend && .venv/bin/python -c "from app.advisor.routes import router; print('ok')"`  
Expected: `ok`

- [ ] **Step 3: Commit**（仅当用户要求时）

---

### Task 3: 前端 API + StarToggle

**Files:**
- Modify: `frontend-advisor/src/api.ts`
- Create: `frontend-advisor/src/components/StarToggle.tsx`
- Create: `frontend-advisor/src/components/StarToggle.test.tsx`
- Modify: `frontend-advisor/src/styles.css`（`.star-toggle`）

**Interfaces:**
- Produces types: `WatchlistItem`, `WatchlistResponse`, `WatchlistMarksResponse`, `WatchlistStatusResponse`
- Produces: `fetchWatchlist`, `fetchWatchlistMarks`, `fetchWatchlistStatus(symbols: string[])`, `addWatchlist(symbol, name?)`, `removeWatchlist(symbol)`
- Produces component:
  ```tsx
  StarToggle({ symbol, starred, busy?, onToggle }: {
    symbol: string
    starred: boolean
    busy?: boolean
    onToggle: (next: boolean) => void | Promise<void>
  })
  ```

- [ ] **Step 1: API 类型与函数**（放在 `fetchPortfolioMarks` 附近）

```ts
export type WatchlistItem = {
  symbol: string
  name?: string | null
  added_at?: string | null
}

export type WatchlistResponse = { items: WatchlistItem[] }

export type WatchlistMarkItem = WatchlistItem & {
  price: number | null
  pre_close: number | null
  day_chg_pct: number | null
  error?: string | null
}

export type WatchlistMarksResponse = {
  session: { is_trading: boolean; now?: string; [k: string]: unknown }
  updated_at?: string | null
  count: number
  items: WatchlistMarkItem[]
}

export type WatchlistStatusResponse = { starred: Record<string, boolean> }

export function fetchWatchlist(): Promise<WatchlistResponse> {
  return authFetch('/api/advisor/watchlist')
}
export function fetchWatchlistMarks(): Promise<WatchlistMarksResponse> {
  return authFetch('/api/advisor/watchlist/marks')
}
export function fetchWatchlistStatus(symbols: string[]): Promise<WatchlistStatusResponse> {
  const q = encodeURIComponent(symbols.join(','))
  return authFetch(`/api/advisor/watchlist/status?symbols=${q}`)
}
export function addWatchlist(symbol: string, name?: string): Promise<WatchlistResponse> {
  const qs = name ? `?name=${encodeURIComponent(name)}` : ''
  return authFetch(`/api/advisor/watchlist/${encodeURIComponent(symbol)}${qs}`, {
    method: 'POST',
  })
}
export function removeWatchlist(symbol: string): Promise<WatchlistResponse> {
  return authFetch(`/api/advisor/watchlist/${encodeURIComponent(symbol)}`, {
    method: 'DELETE',
  })
}
```

- [ ] **Step 2: StarToggle + 单测**

```tsx
// StarToggle.tsx — button.star-toggle，aria-pressed={starred}
// aria-label={starred ? `取消收藏 ${symbol}` : `收藏 ${symbol}`}
// 显示 ★ / ☆（或 SVG）；disabled={busy}；onClick → onToggle(!starred)
```

```tsx
// StarToggle.test.tsx
it('点击触发 onToggle 取反', async () => {
  const onToggle = vi.fn()
  render(<StarToggle symbol="510300" starred={false} onToggle={onToggle} />)
  await userEvent.click(screen.getByRole('button', { name: '收藏 510300' }))
  expect(onToggle).toHaveBeenCalledWith(true)
})
```

CSS：`.star-toggle` 透明底、品牌色实心、muted 空心、无边框。

- [ ] **Step 3: 跑测**

Run: `cd frontend-advisor && npx vitest run src/components/StarToggle.test.tsx`  
Expected: PASS

- [ ] **Step 4: Commit**（仅当用户要求时）

---

### Task 4: 今日关注 + 标的诊断挂星

**Files:**
- Modify: `frontend-advisor/src/components/RecommendationCard.tsx`
- Modify: `frontend-advisor/src/components/RecommendationCard.test.tsx`
- Modify: `frontend-advisor/src/pages/RecommendationsPage.tsx`（含 `BoardTable`）
- Modify: `frontend-advisor/src/components/AdviceCard.tsx`
- Modify: `frontend-advisor/src/pages/AdvicePage.tsx`

**Interfaces:**
- `RecommendationCard` 增加可选：`starred?: boolean`、`onToggleStar?: (next: boolean) => void`
- `AdviceCard` 同理
- 页面维护 `Record<string, boolean>` star map；toggle 时 `addWatchlist` / `removeWatchlist`，乐观更新，失败回滚

- [ ] **Step 1: RecommendationCard / BoardTable**

在 footer / `row-actions` 最前插入：

```tsx
{onToggleStar ? (
  <StarToggle
    symbol={item.symbol}
    starred={Boolean(starred)}
    onToggle={onToggleStar}
  />
) : null}
```

`RecommendationsPage`：items 变化后 `fetchWatchlistStatus(items.map(i => i.symbol))`；提供 `toggleStar(symbol, next)`。

- [ ] **Step 2: AdviceCard / AdvicePage**

`AdvicePage` 在 `item` 加载成功后 `fetchWatchlistStatus([item.symbol])`；把 `starred` + `onToggleStar` 传给 `AdviceCard`（放在 `advice-card-actions` 与「查看K线」并列）。

- [ ] **Step 3: 更新 RecommendationCard 测试**（有星标按钮时可断言）

- [ ] **Step 4: 构建**

Run: `cd frontend-advisor && npm run build`  
Expected: success

- [ ] **Step 5: Commit**（仅当用户要求时）

---

### Task 5: WatchlistPage + 导航

**Files:**
- Create: `frontend-advisor/src/pages/WatchlistPage.tsx`
- Modify: `frontend-advisor/src/App.tsx`
- Modify: `frontend-advisor/src/styles.css`（可复用 `portfolio-marks-table` / `cell-main`）

**Interfaces:**
- 路由 `/watchlist`，nav 文案「我的收藏」，紧挨「我的持仓」
- 页面行为对齐 `PortfolioPage` marks 刷新：`loading` 结束后拉 marks；`session.is_trading` 则 3s `setTimeout` 递归

- [ ] **Step 1: WatchlistPage**

表格列：名称/代码、现价/涨跌幅、操作（`StarToggle` 实心取消、诊断 Link、查看K线）。  
空列表提示去今日关注收藏。  
Meta：更新时间 · 交易中/非交易提示。

- [ ] **Step 2: App.tsx**

```tsx
import WatchlistPage from './pages/WatchlistPage'
// nav:
<NavLink to="/watchlist">我的收藏</NavLink>
// route:
<Route path="/watchlist" element={<WatchlistPage />} />
```

- [ ] **Step 3: build**

Run: `cd frontend-advisor && npm run build`  
Expected: success

- [ ] **Step 4: Commit**（仅当用户要求时）

---

### Task 6: Agent 工具 + 系统提示

**Files:**
- Modify: `backend/app/advisor/agent/tools.py`
- Modify: `backend/app/advisor/agent/graph.py`（`SYSTEM_PROMPT`）

**Interfaces:**
- Tools（JSON 字符串返回，**无 confirm**）：
  - `get_watchlist()` → 可调用 `watchlist_marks` 附带 price/day_chg_pct；失败则退回 `load_watchlist`
  - `add_watchlist_symbol(symbol: str)` → `add_symbol`；捕获 ValueError
  - `remove_watchlist_symbol(symbol: str)` → `remove_symbol`
- 加入 `return [...]` 列表（紧挨 `get_portfolio_summary` 附近）
- SYSTEM_PROMPT 新增一条，例如：
  - `18. 收藏/自选：查看用 get_watchlist；收藏用 add_watchlist_symbol；取消用 remove_watchlist_symbol。写操作无需 confirm，但须先工具成功再口头确认。收藏 ≠ 真实持仓，勿写入 portfolios。`
- 规则 3 写操作列表**不要**把收藏加进「必须 confirm」那句

- [ ] **Step 1: 实现三个 @tool 并挂载**

```python
@tool
def get_watchlist() -> str:
    """获取用户股票收藏列表（含现价/涨跌幅，若可得）。"""
    _bind()
    from ..watchlist import watchlist_marks, load_watchlist
    try:
        return json.dumps(watchlist_marks(user_id), ensure_ascii=False, default=str)
    except Exception:
        return json.dumps(load_watchlist(user_id), ensure_ascii=False, default=str)

@tool
def add_watchlist_symbol(symbol: str) -> str:
    """将标的加入用户收藏（无需 confirm）。"""
    _bind()
    from ..watchlist import add_symbol
    try:
        out = add_symbol(user_id, symbol)
        return json.dumps({"ok": True, **out}, ensure_ascii=False, default=str)
    except ValueError as exc:
        return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)

@tool
def remove_watchlist_symbol(symbol: str) -> str:
    """从用户收藏移除标的（无需 confirm；幂等）。"""
    _bind()
    from ..watchlist import remove_symbol
    out = remove_symbol(user_id, symbol)
    return json.dumps({"ok": True, **out}, ensure_ascii=False, default=str)
```

- [ ] **Step 2: 更新 SYSTEM_PROMPT**

- [ ] **Step 3: 验证 tools 可构建**

Run: `cd backend && .venv/bin/python -c "from app.advisor.agent.tools import build_tools; print([t.name for t in build_tools('u') if 'watchlist' in t.name])"`  
Expected: 打印三个工具名

- [ ] **Step 4: 全量相关测试 + 前端 build**

```bash
cd backend && .venv/bin/python -m pytest tests/test_watchlist.py tests/test_portfolio_marks.py -q
cd ../frontend-advisor && npm run build && npx vitest run src/components/StarToggle.test.tsx src/components/RecommendationCard.test.tsx
```

Expected: 全部 PASS / build OK

- [ ] **Step 5: Commit**（仅当用户要求时）

```bash
git commit -m "feat: add stock watchlist with UI stars and agent tools"
```

---

## Spec coverage check

| Spec 项 | Task |
|---------|------|
| `watchlists` 存储 + 100 上限 + 幂等 | 1 |
| HTTP API 含 status/marks | 2 |
| 星标组件 | 3 |
| 今日关注 / 诊断挂点 | 4 |
| 我的收藏页 + 导航 + 行情刷新 | 5 |
| Agent 三工具 + 提示无 confirm | 6 |
| 非目标（备注/分组等） | 不做 |

## Placeholder scan

无 TBD / 「类似 Task N」占位；commit 明确为可选。
