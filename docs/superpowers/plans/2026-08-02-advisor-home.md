# 次日顾问市场首页 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a market-cockpit home at `/` for the advisor base panel, move 今日关注 to `/recommendations`, and load each home tile independently.

**Architecture:** Frontend `HomePage` fires parallel fetches (market / regime summary / limit-up / sectors). Backend adds a thin sectors Top-N route and a fast regime summary (`get_regime_for_gate`) so the home trend/gate tile does not live-collect. Existing pages keep behavior; only routes and CTA targets change.

**Tech Stack:** FastAPI + pytest (`backend`), React + TypeScript + Vitest (`frontend-advisor`)

## Global Constraints

- Spec: `docs/superpowers/specs/2026-08-02-advisor-home-design.md`
- Layout **A**：上二下二；窄屏单列顺序 指数 → 趋势情绪闸门 → 涨跌 → 连板/热点
- `/` = `HomePage`；今日关注 = `/recommendations`
- 各块独立加载：禁止整页 await-all；单块失败不空白整页
- 文案中文，复用 `regimeCopy`；不裸奔英文枚举
- 不塞推荐列表 / 持仓 / Agent；不做 home 大聚合接口
- 沿用现有 `page` / `diag-block` / `meta-line` 视觉；Docker 镜像标签规则与本功能无关

## File map

| File | Role |
|------|------|
| Create `backend/app/advisor/home_market.py` | `list_hot_sectors(top)` from industry strength + change_pct |
| Create `backend/tests/test_home_market.py` | Sectors + summary route unit tests |
| Modify `backend/app/advisor/routes.py` | `GET /market/sectors`, `GET /regime/summary` |
| Modify `frontend-advisor/src/api.ts` | `fetchMarket`, `fetchRegimeSummary`, `fetchHomeSectors` |
| Create `frontend-advisor/src/pages/HomePage.tsx` | Market cockpit |
| Create `frontend-advisor/src/pages/HomePage.test.tsx` | Independent-tile tests |
| Modify `frontend-advisor/src/App.tsx` | Routes |
| Modify `frontend-advisor/src/components/TopbarNav.tsx` + test | 首页 / 今日关注 |
| Modify `frontend-advisor/src/pages/RegimePage.tsx` + test | CTA → `/recommendations` |
| Modify `frontend-advisor/src/App.test.tsx` | Default `/` shows home |
| Modify `frontend-advisor/src/styles.css` | `.home-grid` / tile skeleton |

---

### Task 1: Backend hot sectors + regime summary

**Files:**
- Create: `backend/app/advisor/home_market.py`
- Create: `backend/tests/test_home_market.py`
- Modify: `backend/app/advisor/routes.py` (after existing `/regime/*` block ~line 102)

**Interfaces:**
- Produces:
  - `list_hot_sectors(top: int = 8, trade_date: str | None = None) -> dict` with shape:
    ```python
    {
      "trade_date": "YYYY-MM-DD",
      "ok": bool,
      "source": str,
      "items": [
        {"rank": 1, "name": str, "change_pct": float | None, "strength": float | None}
      ],
      "error": str | None,  # optional
    }
    ```
  - Route `GET /api/advisor/market/sectors?top=8` → above dict (auth required)
  - Route `GET /api/advisor/regime/summary` → `get_regime_for_gate(allow_stale=True)` (auth required)

- [ ] **Step 1: Write failing tests**

```python
# backend/tests/test_home_market.py
from __future__ import annotations

from fastapi.testclient import TestClient


def test_list_hot_sectors_sorts_by_change_pct_and_caps_top(monkeypatch):
    from app.advisor import home_market

    monkeypatch.setattr(
        home_market,
        "_raw_industry_rows",
        lambda trade_date=None: [
            {"name": "固态电池", "change_pct": 3.2},
            {"name": "银行", "change_pct": -0.5},
            {"name": "人工智能", "change_pct": 5.1},
            {"name": "煤炭", "change_pct": 1.0},
        ],
    )
    out = home_market.list_hot_sectors(top=2)
    assert out["ok"] is True
    assert [x["name"] for x in out["items"]] == ["人工智能", "固态电池"]
    assert out["items"][0]["rank"] == 1
    assert out["items"][0]["change_pct"] == 5.1
    assert out["items"][0]["strength"] == 1.0  # top rank among 4 → pct rank 1.0


def test_list_hot_sectors_empty(monkeypatch):
    from app.advisor import home_market

    monkeypatch.setattr(home_market, "_raw_industry_rows", lambda trade_date=None: [])
    out = home_market.list_hot_sectors(top=8)
    assert out["ok"] is False
    assert out["items"] == []


def test_sectors_and_summary_routes(monkeypatch):
    from app.main import app
    from app.advisor import routes

    def _user():
        return {"id": "u1", "username": "t"}

    app.dependency_overrides[routes._user] = _user
    monkeypatch.setattr(
        routes,
        "list_hot_sectors",
        lambda top=8: {
            "trade_date": "2026-08-01",
            "ok": True,
            "source": "test",
            "items": [{"rank": 1, "name": "人工智能", "change_pct": 5.1, "strength": 1.0}],
        },
    )
    monkeypatch.setattr(
        routes,
        "get_regime_for_gate",
        lambda allow_stale=True: {
            "gate_level": "normal",
            "trend_regime": "range",
            "sentiment_cycle": "strengthen",
            "position_cap": 0.7,
            "data_quality": "ok",
            "metrics": {"breadth": 0.55, "max_board": 9, "promotion_rate": 0.14, "limit_up_count": 80},
        },
    )
    try:
        client = TestClient(app)
        s = client.get("/api/advisor/market/sectors?top=3")
        assert s.status_code == 200
        assert s.json()["items"][0]["name"] == "人工智能"
        r = client.get("/api/advisor/regime/summary")
        assert r.status_code == 200
        assert r.json()["gate_level"] == "normal"
    finally:
        app.dependency_overrides.clear()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && .venv/bin/python -m pytest -q tests/test_home_market.py`
Expected: FAIL (module / routes missing)

- [ ] **Step 3: Implement `home_market.py`**

```python
# backend/app/advisor/home_market.py
from __future__ import annotations

from typing import Any

from ..calendar_util import last_trading_day
from .market_context import fetch_industry_strength_map


def _session_ak():
    from .market_context import _session_ak as _ak
    return _ak()


def _raw_industry_rows(trade_date: str | None = None) -> list[dict[str, Any]]:
    """Return [{name, change_pct}] for ranking. Monkeypatch in unit tests."""
    _ = trade_date
    try:
        ak = _session_ak()
        df = None
        for caller in (
            lambda: ak.stock_board_industry_name_em(),
            lambda: ak.stock_board_industry_spot_em(),
        ):
            try:
                df = caller()
                if df is not None and not df.empty:
                    break
            except Exception:
                continue
        if df is None or df.empty:
            return []
        name_col = "板块名称" if "板块名称" in df.columns else df.columns[0]
        pct_col = None
        for c in ("涨跌幅", "涨跌幅%", "涨跌幅％"):
            if c in df.columns:
                pct_col = c
                break
        if pct_col is None:
            for c in df.columns:
                if "涨" in str(c):
                    pct_col = c
                    break
        if pct_col is None:
            return []
        rows: list[dict[str, Any]] = []
        for _, row in df.iterrows():
            try:
                pct = float(str(row[pct_col]).replace("%", ""))
            except (TypeError, ValueError):
                continue
            name = str(row[name_col]).strip()
            if not name:
                continue
            rows.append({"name": name, "change_pct": pct})
        return rows
    except Exception:
        return []


def list_hot_sectors(top: int = 8, trade_date: str | None = None) -> dict[str, Any]:
    day = (trade_date or last_trading_day())[:10]
    n = max(1, min(int(top or 8), 30))
    rows = list(_raw_industry_rows(day) or [])
    if not rows:
        strength = fetch_industry_strength_map(day)
        by_name = dict(strength.get("by_name") or {})
        if not by_name:
            return {
                "trade_date": day,
                "ok": False,
                "source": strength.get("source") or "industry_strength",
                "items": [],
                "error": strength.get("error") or "empty",
            }
        ranked = sorted(by_name.items(), key=lambda kv: float(kv[1]), reverse=True)[:n]
        items = [
            {
                "rank": i + 1,
                "name": name,
                "change_pct": None,
                "strength": float(score),
            }
            for i, (name, score) in enumerate(ranked)
        ]
        return {
            "trade_date": day,
            "ok": True,
            "source": strength.get("source") or "industry_strength",
            "items": items,
        }

    pcts = [float(r["change_pct"]) for r in rows]
    order = sorted(range(len(pcts)), key=lambda i: pcts[i])
    rank_pct = {i: (j + 1) / len(pcts) for j, i in enumerate(order)}
    enriched = [
        {
            "name": str(r["name"]),
            "change_pct": float(r["change_pct"]),
            "strength": round(float(rank_pct[i]), 4),
        }
        for i, r in enumerate(rows)
    ]
    enriched.sort(key=lambda x: float(x["change_pct"]), reverse=True)
    items = [{"rank": i + 1, **row} for i, row in enumerate(enriched[:n])]
    return {
        "trade_date": day,
        "ok": True,
        "source": "akshare.stock_board_industry",
        "items": items,
    }
```

Do not call live network inside unit tests — monkeypatch `_raw_industry_rows`.

- [ ] **Step 4: Wire routes**

In `routes.py`:

```python
from .home_market import list_hot_sectors
from .regime import get_regime_for_gate  # or import inside handlers

@router.get("/regime/summary")
def regime_summary(user: dict[str, Any] = Depends(_user)) -> dict[str, Any]:
    from .regime import get_regime_for_gate
    return get_regime_for_gate(allow_stale=True)

@router.get("/market/sectors")
def market_sectors(
    top: int = Query(default=8, ge=1, le=30),
    user: dict[str, Any] = Depends(_user),
) -> dict[str, Any]:
    return list_hot_sectors(top=top)
```

Export `list_hot_sectors` / `get_regime_for_gate` on the `routes` module namespace so tests can monkeypatch `routes.list_hot_sectors` (import at module level).

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && .venv/bin/python -m pytest -q tests/test_home_market.py`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/advisor/home_market.py backend/tests/test_home_market.py backend/app/advisor/routes.py
git commit -m "$(cat <<'EOF'
feat(advisor): add home sectors and fast regime summary APIs

EOF
)"
```

---

### Task 2: Frontend API helpers

**Files:**
- Modify: `frontend-advisor/src/api.ts`
- Create: `frontend-advisor/src/api.home.test.ts` (or extend an existing small api test if present; otherwise page tests cover enough — prefer a tiny unit file)

**Interfaces:**
- Produces:
  - `MarketIndexItem`, `MarketResponse`
  - `fetchMarket(): Promise<MarketResponse>` → `GET /api/market` (no advisor prefix; still via `authFetch` or plain fetch — use same pattern as other non-advisor calls if any; otherwise `authFetch('/api/market')`)
  - `fetchRegimeSummary(): Promise<RegimeCurrent>` → `/api/advisor/regime/summary`
  - `HomeSectorItem`, `HomeSectorsResponse`
  - `fetchHomeSectors(top = 8): Promise<HomeSectorsResponse>`

- [ ] **Step 1: Write failing type-level / import smoke test**

```ts
// frontend-advisor/src/api.home.test.ts
import { describe, expect, it, vi, beforeEach } from 'vitest'
import * as auth from './auth'

vi.mock('./auth', async () => {
  const actual = await vi.importActual<typeof import('./auth')>('./auth')
  return { ...actual, authFetch: vi.fn(), getToken: () => 't' }
})

describe('home api helpers', () => {
  beforeEach(() => {
    vi.mocked(auth.authFetch).mockReset()
  })

  it('fetchHomeSectors hits advisor market/sectors', async () => {
    vi.mocked(auth.authFetch).mockResolvedValue({
      trade_date: '2026-08-01',
      ok: true,
      source: 't',
      items: [],
    })
    const { fetchHomeSectors } = await import('./api')
    await fetchHomeSectors(5)
    expect(auth.authFetch).toHaveBeenCalledWith(
      expect.stringContaining('/api/advisor/market/sectors?top=5'),
    )
  })

  it('fetchRegimeSummary hits regime/summary', async () => {
    vi.mocked(auth.authFetch).mockResolvedValue({ gate_level: 'normal' })
    const { fetchRegimeSummary } = await import('./api')
    await fetchRegimeSummary()
    expect(auth.authFetch).toHaveBeenCalledWith('/api/advisor/regime/summary')
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend-advisor && npm test -- --run src/api.home.test.ts`
Expected: FAIL (exports missing)

- [ ] **Step 3: Add helpers near existing regime helpers in `api.ts`**

```ts
export type MarketIndexItem = {
  symbol: string
  name: string
  price?: number | null
  change?: number | null
  change_pct?: number | null
  featured?: boolean
}

export type MarketResponse = {
  featured?: MarketIndexItem[]
  as_of?: string
  source?: string
}

export function fetchMarket(): Promise<MarketResponse> {
  return authFetch('/api/market')
}

export function fetchRegimeSummary(): Promise<RegimeCurrent> {
  return authFetch('/api/advisor/regime/summary')
}

export type HomeSectorItem = {
  rank: number
  name: string
  change_pct: number | null
  strength: number | null
}

export type HomeSectorsResponse = {
  trade_date: string
  ok: boolean
  source: string
  items: HomeSectorItem[]
  error?: string | null
}

export function fetchHomeSectors(top = 8): Promise<HomeSectorsResponse> {
  const q = new URLSearchParams({ top: String(top) })
  return authFetch(`/api/advisor/market/sectors?${q}`)
}
```

Reuse existing `RegimeCurrent` / `fetchLimitUp` types — do not duplicate.

- [ ] **Step 4: Run tests**

Run: `cd frontend-advisor && npm test -- --run src/api.home.test.ts`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend-advisor/src/api.ts frontend-advisor/src/api.home.test.ts
git commit -m "$(cat <<'EOF'
feat(advisor-ui): add market home API client helpers

EOF
)"
```

---

### Task 3: `HomePage` cockpit UI + independent loading tests

**Files:**
- Create: `frontend-advisor/src/pages/HomePage.tsx`
- Create: `frontend-advisor/src/pages/HomePage.test.tsx`
- Modify: `frontend-advisor/src/styles.css` (append `.home-*` rules)

**Interfaces:**
- Consumes: `fetchMarket`, `fetchRegimeSummary`, `fetchLimitUp`, `fetchHomeSectors`, `regimeCopy` (`gateOneLiner`, `trendLabel`, `sentimentLabel`, `gateShortLabel`, `formatCapPct`)
- Consumes: `klineHref` / explorer link helper from `explorerLinks.ts` for index symbols
- Produces: default export `HomePage`

- [ ] **Step 1: Write failing page tests**

```tsx
// frontend-advisor/src/pages/HomePage.test.tsx
import '@testing-library/jest-dom/vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../api'
import HomePage from './HomePage'

vi.mock('../api', async () => {
  const actual = await vi.importActual<typeof import('../api')>('../api')
  return {
    ...actual,
    fetchMarket: vi.fn(),
    fetchRegimeSummary: vi.fn(),
    fetchLimitUp: vi.fn(),
    fetchHomeSectors: vi.fn(),
  }
})

describe('HomePage', () => {
  beforeEach(() => {
    vi.mocked(api.fetchMarket).mockReset()
    vi.mocked(api.fetchRegimeSummary).mockReset()
    vi.mocked(api.fetchLimitUp).mockReset()
    vi.mocked(api.fetchHomeSectors).mockReset()
  })

  it('renders market tiles even when regime summary hangs', async () => {
    vi.mocked(api.fetchMarket).mockResolvedValue({
      featured: [
        { symbol: '000300', name: '沪深300', price: 4588, change_pct: -0.8 },
      ],
    })
    vi.mocked(api.fetchRegimeSummary).mockImplementation(() => new Promise(() => {}))
    vi.mocked(api.fetchLimitUp).mockResolvedValue({
      source: 'akshare',
      as_of: '2026-08-01T10:00:00+08:00',
      date: '20260801',
      session: { is_trading: false, is_trading_day: true },
      today: [],
      ladder: [{ board_count: 5, items: [] }],
    })
    vi.mocked(api.fetchHomeSectors).mockResolvedValue({
      trade_date: '2026-08-01',
      ok: true,
      source: 't',
      items: [{ rank: 1, name: '人工智能', change_pct: 5.1, strength: 1 }],
    })

    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    )

    await waitFor(() => {
      expect(screen.getByText('沪深300')).toBeInTheDocument()
    })
    expect(screen.getByText('人工智能')).toBeInTheDocument()
    expect(screen.getByText(/趋势|情绪|闸门/)).toBeInTheDocument() // skeleton or label still visible
  })

  it('links to regime and limitup', async () => {
    vi.mocked(api.fetchMarket).mockResolvedValue({ featured: [] })
    vi.mocked(api.fetchRegimeSummary).mockResolvedValue({
      gate_level: 'normal',
      trend_regime: 'range',
      sentiment_cycle: 'strengthen',
      position_cap: 0.7,
      data_quality: 'ok',
      metrics: { breadth: 0.52, max_board: 7, promotion_rate: 0.2, limit_up_count: 60 },
      evidence: [],
    } as api.RegimeCurrent)
    vi.mocked(api.fetchLimitUp).mockResolvedValue({
      source: 'akshare',
      as_of: '2026-08-01T10:00:00+08:00',
      date: '20260801',
      session: { is_trading: false, is_trading_day: true },
      today: [],
      ladder: [],
    })
    vi.mocked(api.fetchHomeSectors).mockResolvedValue({
      trade_date: '2026-08-01',
      ok: true,
      source: 't',
      items: [],
    })

    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    )

    await waitFor(() => {
      expect(screen.getByRole('link', { name: '查看今日闸门' })).toHaveAttribute(
        'href',
        '/regime',
      )
    })
    expect(screen.getByRole('link', { name: /打板/ })).toHaveAttribute('href', '/limitup')
  })
})
```

Adjust assertions to match final copy; keep the **hanging regime** independence guarantee.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend-advisor && npm test -- --run src/pages/HomePage.test.tsx`
Expected: FAIL (module missing)

- [ ] **Step 3: Implement `HomePage.tsx`**

Structure (conceptual — implement fully):

```tsx
export default function HomePage() {
  // four independent useEffects / loaders — do NOT Promise.all before setState
  // state per tile: { status: 'loading'|'ok'|'error', data?, error? }

  return (
    <section className="page home-page">
      <div className="page-hero">
        <h2 className="section-title">市场首页</h2>
        <p className="meta-line">/* as_of / trade_date from first available tile */</p>
      </div>
      <div className="home-grid">
        <Tile title="主要指数">...</Tile>
        <Tile title="趋势 · 情绪 · 闸门">
          {/* trendLabel, sentimentLabel, gateOneLiner, formatCapPct */}
          <Link to="/regime">查看今日闸门</Link>
        </Tile>
        <Tile title="涨跌分布">
          {/* prefer regime metrics.breadth → show as 上涨占比 xx%；note「摘要」 if from regime */}
        </Tile>
        <Tile title="情绪结构 · 热点">
          {/* max_board, promotion_rate, limit_up_count from regime; ladder height from limitup */}
          {/* sector list */}
          <Link to="/limitup">打开打板</Link>
        </Tile>
      </div>
    </section>
  )
}
```

CSS append:

```css
.home-grid {
  display: grid;
  grid-template-columns: 1.2fr 1fr;
  gap: 12px;
}
@media (max-width: 899px) {
  .home-grid { grid-template-columns: 1fr; }
}
.home-tile {
  border: 1px solid var(--line);
  border-radius: 10px;
  padding: 12px 14px;
  background: var(--bg1);
  min-height: 140px;
}
.home-tile-skeleton {
  height: 72px;
  border-radius: 6px;
  background: var(--bg2);
}
```

Index row: show up to 6 featured (沪深300/上证/深成/创业板优先若存在). Link name via `klineHref(symbol)`.

- [ ] **Step 4: Run tests**

Run: `cd frontend-advisor && npm test -- --run src/pages/HomePage.test.tsx`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend-advisor/src/pages/HomePage.tsx frontend-advisor/src/pages/HomePage.test.tsx frontend-advisor/src/styles.css
git commit -m "$(cat <<'EOF'
feat(advisor-ui): add market cockpit HomePage with independent tiles

EOF
)"
```

---

### Task 4: Wire routes, nav, and regime CTAs

**Files:**
- Modify: `frontend-advisor/src/App.tsx`
- Modify: `frontend-advisor/src/components/TopbarNav.tsx`
- Modify: `frontend-advisor/src/components/TopbarNav.test.tsx`
- Modify: `frontend-advisor/src/pages/RegimePage.tsx`
- Modify: `frontend-advisor/src/pages/RegimePage.test.tsx`
- Modify: `frontend-advisor/src/App.test.tsx` (mock `HomePage`, assert default route)
- Grep + fix any other `/?regime_override` or 「今日关注」→`/` links

**Interfaces:**
- `BASE_NAV_LINKS[0]` = 首页 `/`
- `BASE_NAV_LINKS[1]` = 今日关注 `/recommendations`
- `App` route `/` → `<HomePage />`, `/recommendations` → `<RecommendationsPage />`

- [ ] **Step 1: Update nav + failing tests**

```ts
export const BASE_NAV_LINKS: TopbarNavLink[] = [
  { to: '/', end: true, label: '首页' },
  { to: '/recommendations', label: '今日关注' },
  { to: '/advice', label: '股票诊断' },
  // ...rest unchanged
]
```

In `TopbarNav.test.tsx`, assert links include 首页 → `/` and 今日关注 → `/recommendations`.

In `RegimePage.tsx`:

```ts
navigate('/recommendations?regime_override=1')
// and
navigate('/recommendations')
```

Update `RegimePage.test.tsx` location probe path if it expected `/?regime_override=1`.

- [ ] **Step 2: Wire App routes**

```tsx
import HomePage from './pages/HomePage'
// ...
<Route path="/" element={<HomePage />} />
<Route path="/recommendations" element={<RecommendationsPage />} />
```

In `App.test.tsx`, `vi.mock('./pages/HomePage', () => ({ default: () => <h1>市场首页</h1> }))` and assert landing shows 市场首页; add navigation click to 今日关注 if covered.

- [ ] **Step 3: Run tests**

Run:

```bash
cd frontend-advisor && npm test -- --run \
  src/components/TopbarNav.test.tsx \
  src/pages/RegimePage.test.tsx \
  src/App.test.tsx \
  src/pages/HomePage.test.tsx \
  src/pages/RecommendationsPage.test.tsx
```

Expected: PASS  
Note: `RecommendationsPage` tests use `MemoryRouter initialEntries={['/']}` — update to `['/recommendations']` or `['/recommendations?regime_override=1']`.

- [ ] **Step 4: Commit**

```bash
git add frontend-advisor/src/App.tsx frontend-advisor/src/App.test.tsx \
  frontend-advisor/src/components/TopbarNav.tsx frontend-advisor/src/components/TopbarNav.test.tsx \
  frontend-advisor/src/pages/RegimePage.tsx frontend-advisor/src/pages/RegimePage.test.tsx \
  frontend-advisor/src/pages/RecommendationsPage.test.tsx
git commit -m "$(cat <<'EOF'
feat(advisor-ui): default / to market home; move recommendations route

EOF
)"
```

---

### Task 5: Acceptance sweep

**Files:** none new (fix only if sweep finds gaps)

- [ ] **Step 1: Backend suite for touched modules**

Run: `cd backend && .venv/bin/python -m pytest -q tests/test_home_market.py tests/test_regime_service.py tests/test_regime_routes.py`
Expected: PASS

- [ ] **Step 2: Frontend build**

Run: `cd frontend-advisor && npm run build`
Expected: exit 0

- [ ] **Step 3: Manual checklist (document in commit body if needed)**

1. Open `http://127.0.0.1:5174/` → 市场首页四块骨架后填数  
2. 人为断掉 summary 时指数/热点仍出  
3. 导航「今日关注」→ `/recommendations` 表格可用  
4. 闸门页「仍要看今日关注」→ `/recommendations?regime_override=1`  
5. 首页链到 `/regime`、`/limitup`

- [ ] **Step 4: Final commit only if fixes landed; else done**

---

## Spec coverage self-check

| Spec requirement | Task |
|------------------|------|
| Layout A four tiles | Task 3 |
| Independent loading | Task 3 tests |
| `/` home, `/recommendations` | Task 4 |
| Nav labels | Task 4 |
| Market indices API | Task 2–3 |
| Regime summary fast path | Task 1–3 |
| Breadth from regime metrics | Task 3 |
| Limit-up + metrics | Task 3 |
| Hot sectors API | Task 1–3 |
| Regime/LimitUp links | Task 3–4 |
| CTA path migration | Task 4 |
| No home aggregate mega-API | honored |
| Chinese copy via regimeCopy | Task 3 |

## Placeholder scan

No TBD / NotImplementedError / “similar to Task N” leftovers; Task 1 includes full `_raw_industry_rows` body.
