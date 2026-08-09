# 打板进度 SSE 与晋级思考流 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 基础打板页展示真实拉取阶段进度；打板晋级页流式展示阶段与模型思考，完成后给出候选表。

**Architecture:** 复用龙虎榜式 SSE。`iter_limit_up_events` / `iter_promote_events` 产出 meta/progress/thinking/done；前端 `streamLimitUp` / `streamLimitUpPromote` 消费并更新状态行与可折叠思考面板。盘中打板轮询仍走短缓存 GET。

**Tech Stack:** FastAPI StreamingResponse、现有 limitup / limitup_promote、React + vitest、ChatOpenAI stream

**Spec:** `docs/superpowers/specs/2026-08-09-limitup-progress-and-promote-thinking-design.md`

## Global Constraints

- 文案：研究观察、不保证次日涨停、非投资建议与下单指令
- 不拉长打板 6s 缓存；不引入 WebSocket；不改基础打板表格必选列
- 晋级无 Key → 403 / error「请先配置 DeepSeek API Key」
- 镜像标签约定与本任务无关

## File map

| File | Role |
|------|------|
| `backend/app/limitup.py` | `iter_limit_up_events`；资金流进度回调 |
| `backend/app/main.py` | `GET /api/{source}/limit-up/stream` |
| `backend/app/providers/akshare_provider.py` | 可选透传 stream（若路由直接调 service 则可跳过） |
| `backend/tests/test_limitup.py` | stream 事件顺序与缓存 |
| `backend/app/advisor/limitup_promote.py` | `iter_promote_events` |
| `backend/app/advisor/routes.py` | `GET .../limitup/promote/stream` |
| `backend/tests/test_limitup_promote.py` | thinking / cache / no key |
| `frontend-advisor/src/api.ts` | `streamLimitUp` / `streamLimitUpPromote` |
| `frontend-advisor/src/pages/LimitUpPage.tsx` | 首屏/刷新走 stream + 进度文案 |
| `frontend-advisor/src/pages/LimitUpPromotePage.tsx` | 进度 + 思考面板 |
| 对应 `*.test.tsx` | UI 断言 |

---

### Task 1: 打板 `iter_limit_up_events` + 路由

**Files:**
- Modify: `backend/app/limitup.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_limitup.py`

**Produces:** `iter_limit_up_events(*, force: bool = False) -> Iterator[dict]` events `{event, data}`

- [x] **Step 1:** 写测试：缓存 miss 时 progress phases 含 `pool`→`fund_flow`→`build`，最后 `done`；缓存 hit 时 `meta.cached` 且不调 `_fetch_pools`
- [x] **Step 2:** 实现 `iter_limit_up_events`；`enrich_fund_flow` 增加可选 `on_progress(done, total)`；`get_limit_up` 仍同步走同一构建路径或内部复用
- [x] **Step 3:** `main.py` 增加 stream 路由（对齐 leaderboard SSE headers）
- [x] **Step 4:** `pytest backend/tests/test_limitup.py -q` 通过

---

### Task 2: 晋级 `iter_promote_events` + 路由

**Files:**
- Modify: `backend/app/advisor/limitup_promote.py`
- Modify: `backend/app/advisor/routes.py`
- Test: `backend/tests/test_limitup_promote.py`

**Produces:** `iter_promote_events(user_id: str, *, force: bool = False)`

- [x] **Step 1:** 测试：mock stream chunks（content + reasoning_content）→ 收到 thinking deltas + done picks；cache hit 不调 model；无 key ValueError
- [x] **Step 2:** 实现 stream 路径；抽取 reasoning from `additional_kwargs` / `response_metadata`
- [x] **Step 3:** 挂 `GET /limitup/promote/stream`
- [x] **Step 4:** pytest 通过

---

### Task 3: 前端打板进度

**Files:**
- Modify: `frontend-advisor/src/api.ts`
- Modify: `frontend-advisor/src/pages/LimitUpPage.tsx`
- Modify: `frontend-advisor/src/pages/LimitUpPage.test.tsx`

- [x] **Step 1:** `streamLimitUp`（对齐 `streamLeaderboard`）
- [x] **Step 2:** 首屏/手动刷新用 stream；轮询仍 `fetchLimitUp`；展示 status 进度
- [x] **Step 3:** 测试 mock stream 展示阶段文案
- [x] **Step 4:** `npm test -- --run src/pages/LimitUpPage.test.tsx` 通过

---

### Task 4: 前端晋级思考面板

**Files:**
- Modify: `frontend-advisor/src/api.ts`
- Modify: `frontend-advisor/src/pages/LimitUpPromotePage.tsx`
- Modify: `frontend-advisor/src/pages/LimitUpPromotePage.test.tsx`

- [x] **Step 1:** `streamLimitUpPromote`
- [x] **Step 2:** 阶段文案 + 可折叠思考过程；done 后表格
- [x] **Step 3:** 测试 thinking 与无 Key
- [x] **Step 4:** vitest 通过

---

### Task 5: 收尾验证

- [x] 后端相关 pytest + 前端相关 vitest 全绿
- [x] 不主动 commit（除非用户要求）
