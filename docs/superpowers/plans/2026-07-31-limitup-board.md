# Limit-Up Board (打板) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an AKShare-only「打板」tab with「当天涨停」(trading-hours, 10s refresh) and「连板看板」(ladder by consecutive boards; readable after close).

**Architecture:** Thin `limitup.py` aggregates Eastmoney zt + zbgc pools via AKShare; `AkshareProvider.get_limit_up` + `GET /api/{source}/limit-up`; explorer frontend new feature/route/page.

**Tech Stack:** FastAPI, AKShare, pytest, React + React Router, Vitest

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-31-limitup-board-design.md`
- Feature only on `akshare`; other sources 404 / no Tab
- Universe: A-share pools only (no ETF scan)
- `day_chg_pct` is decimal ratio (`0.10` = 10%)
- Same symbol: prefer `sealed` over `broken`
- Ladder from **sealed** only, `board_count` descending; empty tiers omitted
- K-line link: `/${source}/kline?symbol=${symbol}&range=daily`
- Server short cache ~5–8s

## File map

| File | Role |
|------|------|
| Create `backend/app/limitup.py` | Fetch pools, normalize, ladder, TTL cache |
| Create `backend/tests/test_limitup.py` | Unit tests with mocked DataFrames |
| Modify `backend/app/providers/akshare_provider.py` | `features` + `get_limit_up` |
| Modify `backend/app/main.py` | Route `GET /api/{source}/limit-up` |
| Modify `frontend/src/sources.ts` | `SourceFeature` + `sourcePath` |
| Modify `frontend/src/components/PageNav.tsx` | Tab「打板」 |
| Modify `frontend/src/main.tsx` | Route |
| Create `frontend/src/limitUpApi.ts` | Typed fetch helper |
| Create `frontend/src/LimitUpPage.tsx` | Two tables + polling |
| Create `frontend/src/LimitUpPage.test.tsx` | Visibility / markers |
| Modify `frontend/src/styles.css` | Minimal layout styles |

---

### Task 1: Backend `limitup` service + tests

**Files:**
- Create: `backend/app/limitup.py`
- Test: `backend/tests/test_limitup.py`

**Interfaces:**
- Produces: `get_limit_up() -> dict` with keys `as_of`, `session`, `today`, `ladder`
- Internal: `_normalize_row`, `_build_ladder`, `_fetch_zt_pool`, `_fetch_zbgc_pool`

- [ ] Write failing tests for sealed-over-broken merge and ladder descending

```python
def test_merge_prefers_sealed():
    # sealed + broken same symbol → one row status sealed

def test_ladder_groups_descending():
    # board_count 3 then 1; no empty tiers
```

- [ ] Implement `limitup.py`: call `ak.stock_zt_pool_em(date=YYYYMMDD)` and `ak.stock_zt_pool_zbgc_em(date=...)` (date from Asia/Shanghai trading calendar / today); map columns flexibly (代码/名称/涨跌幅/连板数); cache TTL 6s
- [ ] Run: `backend/.venv/bin/python -m pytest tests/test_limitup.py -q`
- [ ] Commit: `feat(limitup): aggregate zt and zbgc pools`

---

### Task 2: Provider + HTTP route

**Files:**
- Modify: `backend/app/providers/akshare_provider.py`
- Modify: `backend/app/main.py` (near fund/market routes)
- Test: extend `backend/tests/test_limitup.py` or small route test if pattern exists

**Interfaces:**
- Consumes: `limitup.get_limit_up`
- Produces: `AkshareProvider.features` includes `"limitup"`; `get_limit_up()`; `GET /api/{source}/limit-up`

- [ ] Add feature + method on provider
- [ ] Add route: if `"limitup" not in provider.features` → 404; call `get_limit_up`; catch RuntimeError → 502
- [ ] pytest smoke (mock provider or service)
- [ ] Commit: `feat(limitup): expose GET /api/akshare/limit-up`

---

### Task 3: Frontend wiring (feature, nav, route, API)

**Files:**
- Modify: `frontend/src/sources.ts`
- Modify: `frontend/src/components/PageNav.tsx`
- Modify: `frontend/src/main.tsx`
- Create: `frontend/src/limitUpApi.ts`

**Interfaces:**
- Produces: `fetchLimitUp(source): Promise<LimitUpResponse>`

- [ ] Extend `SourceFeature` with `'limitup'`; `sourcePath` → `/${id}/limitup`; ensure DEFAULT akshare list can show Tab when backend features include it (features come from API health/sources — keep local fallback in sync if any)
- [ ] PageNav link「打板」
- [ ] Route `/:source/limitup` + optional `/limitup` → akshare redirect
- [ ] `limitUpApi.ts` types matching spec
- [ ] Commit: `feat(limitup): nav route and API client`

---

### Task 4: `LimitUpPage` + styles + tests

**Files:**
- Create: `frontend/src/LimitUpPage.tsx`
- Create: `frontend/src/LimitUpPage.test.tsx`
- Modify: `frontend/src/styles.css`

**Interfaces:**
- Consumes: `fetchLimitUp`, `session.is_trading`

- [ ] Page: header via PageNav `activeFeature="limitup"`; section「当天涨停」conditional on `is_trading`; section「连板看板」always when data; 10s interval only while trading; pause when `document.visibilityState === 'hidden'`; after close fetch once then clear interval
- [ ] Columns per spec; status badges 当前涨停 / 曾涨停; K-line `Link`
- [ ] Vitest: mock API — non-trading hides today table; sealed/broken labels render
- [ ] Run: `cd frontend && npx vitest run src/LimitUpPage.test.tsx`
- [ ] Commit: `feat(limitup): limit-up page with polling tables`

---

### Task 5: Smoke + README touch (optional)

- [ ] Manual: with backend up, open `/akshare/limitup` in trading hours (or mock session in unit tests already)
- [ ] If README lists explorer tabs, add「打板」one line; else skip
- [ ] Commit only if README changed

## Spec coverage

| Spec item | Task |
|-----------|------|
| zt + zbgc merge | 1 |
| ladder sealed desc | 1 |
| API + feature | 2 |
| Tab / route | 3 |
| today table trading-only 10s | 4 |
| ladder after close | 4 |
| K-line deep link | 4 |
| No ETF / no history archive | — (non-goals) |
