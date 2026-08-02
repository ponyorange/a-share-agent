# 今日闸门页可读性改版 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rewrite the advisor「市场状态」UI into a decision-first「今日闸门」page with Chinese conclusions, ≤3 why-bullets, and folded metrics — frontend only.

**Architecture:** Centralize copy maps and why-bullet generation in `regimeCopy.ts`; RegimePage consumes it for the new IA; TopbarNav / LimitUp / Recommendations reuse the same labels. No backend API changes.

**Tech Stack:** React + TypeScript + Vitest (`frontend-advisor`)

## Global Constraints

- Spec: `docs/superpowers/specs/2026-08-02-regime-page-ux-design.md`
- Route stays `/regime`; nav + H1 show **今日闸门**
- First screen: 结论 → 仓位 → 一句话 → 三标签 → ≤3 为什么；无英文 raw 枚举
- Metrics/evidence table default **collapsed**
- `risk_off` CTA still navigates to `/?regime_override=1`
- Ratios: decimal `0.40` → display `40%`
- Do not change gate engine / advisor API contracts
- Also commit the untracked bson int-key store fix if still dirty on main (`store.py` / `service.py` `_by_board`) only if present and related — **out of this plan**; leave unless already staged elsewhere

## File map

| File | Role |
|------|------|
| Create `frontend-advisor/src/regimeCopy.ts` | Labels, headlines, why bullets, metric key → 中文 |
| Create `frontend-advisor/src/regimeCopy.test.ts` | Unit tests for copy helpers |
| Modify `frontend-advisor/src/pages/RegimePage.tsx` | Decision-first layout |
| Modify `frontend-advisor/src/pages/RegimePage.test.tsx` | Assert Chinese UI, no raw English on hero |
| Modify `frontend-advisor/src/components/TopbarNav.tsx` + test | Nav label |
| Modify `frontend-advisor/src/pages/LimitUpPage.tsx` + test | Sentiment labels + link text |
| Modify `frontend-advisor/src/pages/RecommendationsPage.tsx` + test | Badge / link Chinese |
| Modify `frontend-advisor/src/styles.css` | Hero / details `<details>` styles |

---

### Task 1: `regimeCopy` helpers + unit tests

**Files:**
- Create: `frontend-advisor/src/regimeCopy.ts`
- Test: `frontend-advisor/src/regimeCopy.test.ts`

**Interfaces:**
- Produces:
  - `gateConclusion(level: string): string`
  - `gateOneLiner(level: string): string`
  - `trendLabel(v: string): string`
  - `sentimentLabel(v: string): string`
  - `dataQualityLabel(v: string): string`
  - `gateShortLabel(level: string): string` — for history/badge（今天先别买 / 轻仓观望 / 正常参与 / 可积极）
  - `formatCapPct(cap: number | null | undefined): string` — e.g. `35%`
  - `buildWhyBullets(input: { gate_level?: string; trend_regime?: string; data_quality?: string; evidence?: {key:string;value:string;note?:string}[]; metrics?: Record<string, unknown> }): string[]` — length 1..3
  - `metricLabel(key: string): string`

- [ ] **Step 1: Write failing tests**

```ts
import { describe, expect, it } from 'vitest'
import {
  gateConclusion,
  gateOneLiner,
  buildWhyBullets,
  trendLabel,
  sentimentLabel,
  dataQualityLabel,
  formatCapPct,
  metricLabel,
} from './regimeCopy'

describe('regimeCopy', () => {
  it('maps gate conclusions', () => {
    expect(gateConclusion('risk_off')).toBe('今天先别急着买')
    expect(gateConclusion('defensive')).toBe('先轻仓观望')
    expect(gateConclusion('normal')).toBe('正常参与即可')
    expect(gateConclusion('aggressive')).toBe('可以积极一点')
  })

  it('formats position cap', () => {
    expect(formatCapPct(0.35)).toBe('35%')
  })

  it('builds at most 3 why bullets from notes', () => {
    const bullets = buildWhyBullets({
      gate_level: 'defensive',
      trend_regime: 'range',
      data_quality: 'ok',
      evidence: [
        { key: 'a', value: '1', note: '涨停变少，赚钱效应转弱' },
        { key: 'b', value: '2', note: '连板高度回落，接力变难' },
        { key: 'c', value: '3', note: '指数走震荡，不宜重仓' },
        { key: 'd', value: '4', note: '多余第四条不应出现' },
      ],
    })
    expect(bullets).toHaveLength(3)
    expect(bullets[0]).toContain('涨停变少')
  })

  it('includes data-quality bullet when degraded', () => {
    const bullets = buildWhyBullets({
      gate_level: 'defensive',
      data_quality: 'degraded',
      evidence: [],
      metrics: {},
    })
    expect(bullets.some((b) => /缺失|降级|保守/.test(b))).toBe(true)
    expect(bullets.length).toBeGreaterThanOrEqual(1)
    expect(bullets.length).toBeLessThanOrEqual(3)
  })

  it('maps trend/sentiment/quality and metric keys', () => {
    expect(trendLabel('range')).toBe('震荡')
    expect(sentimentLabel('ebb')).toBe('退潮')
    expect(dataQualityLabel('ok')).toBe('可用')
    expect(metricLabel('seal_rate')).toBe('封板率')
  })
})
```

- [ ] **Step 2: Run — expect FAIL**

```bash
cd frontend-advisor && npm test -- --run src/regimeCopy.test.ts
```

- [ ] **Step 3: Implement `regimeCopy.ts`** per spec tables (gate one-liners verbatim from spec). For `buildWhyBullets`: prefer evidence notes; else heuristic from keys (`seal_rate`/`limit_up_count`/`promotion`/`max_board`/`trend`); pad with gate fallback; force quality note when not `ok`.

```ts
export function formatCapPct(cap: number | null | undefined): string {
  if (cap == null || Number.isNaN(cap)) return '—'
  return `${Math.round(cap * 100)}%`
}
```

- [ ] **Step 4: Tests PASS**

```bash
cd frontend-advisor && npm test -- --run src/regimeCopy.test.ts
```

- [ ] **Step 5: Commit**

```bash
git add frontend-advisor/src/regimeCopy.ts frontend-advisor/src/regimeCopy.test.ts docs/superpowers/specs/2026-08-02-regime-page-ux-design.md
git commit -m "$(cat <<'EOF'
feat(advisor-ui): add regime Chinese copy helpers

EOF
)"
```

---

### Task 2: RegimePage decision-first layout

**Files:**
- Modify: `frontend-advisor/src/pages/RegimePage.tsx`
- Modify: `frontend-advisor/src/pages/RegimePage.test.tsx`
- Modify: `frontend-advisor/src/styles.css` (minimal)

**Interfaces:**
- Consumes: all helpers from `regimeCopy.ts`
- Removes local GATE/TREND/SENTIMENT maps (import from regimeCopy)

- [ ] **Step 1: Update failing/updated tests first**

```tsx
it('shows decision-first Chinese hero without raw English enums', async () => {
  vi.mocked(api.fetchRegimeCurrent).mockResolvedValue({
    gate_level: 'defensive',
    position_cap: 0.35,
    trend_regime: 'range',
    sentiment_cycle: 'ebb',
    data_quality: 'ok',
    evidence: [{ key: 'seal_rate', value: '0.4', note: '封板偏弱，赚钱效应一般' }],
    override_allowed: true,
  })
  render(<MemoryRouter><RegimePage /></MemoryRouter>)
  expect(await screen.findByRole('heading', { name: '今日闸门' })).toBeInTheDocument()
  expect(screen.getByText('先轻仓观望')).toBeInTheDocument()
  expect(screen.getByText(/建议总仓位不超过\s*35%/)).toBeInTheDocument()
  expect(screen.queryByText(/raw gate_level/i)).not.toBeInTheDocument()
  expect(screen.queryByText('defensive')).not.toBeInTheDocument()
  expect(screen.getByText('为什么这样判')).toBeInTheDocument()
})

it('keeps metrics details collapsed by default', async () => {
  // ... mock current ...
  render(...)
  const details = await screen.findByText('查看指标明细')
  // details element should not show seal_rate English key in open content by default
  expect(screen.queryByRole('columnheader', { name: '指标' })).not.toBeVisible()
  // or: expect(document.querySelector('details')).not.toHaveAttribute('open')
})

it('shows risk_off CTA', async () => { /* keep; assert 今天先别急着买 + button */ })

it('shows recent history in Chinese', async () => {
  // history risk_off row shows 今天先别急着买 or gateShortLabel, not risk_off
})
```

- [ ] **Step 2: Rewrite RegimePage JSX** to match spec IA:
  - H1 今日闸门
  - Hero: conclusion + `建议总仓位不超过 {formatCapPct}`
  - one-liner paragraph
  - three tags (趋势/情绪/数据) Chinese only
  - ordered list why bullets
  - buttons as spec
  - `<details><summary>查看指标明细</summary>…table…</details>`
  - history using `gateShortLabel` / `sentimentLabel`

- [ ] **Step 3: CSS** — `.regime-conclusion`, `.regime-tags`, `.regime-why`, `details.regime-details` spacing only

- [ ] **Step 4: Run tests**

```bash
cd frontend-advisor && npm test -- --run src/pages/RegimePage.test.tsx src/regimeCopy.test.ts
```

- [ ] **Step 5: Commit**

```bash
git add frontend-advisor/src/pages/RegimePage.tsx frontend-advisor/src/pages/RegimePage.test.tsx frontend-advisor/src/styles.css
git commit -m "$(cat <<'EOF'
feat(advisor-ui): decision-first 今日闸门 page layout

EOF
)"
```

---

### Task 3: Nav + LimitUp + Recommendations copy sync

**Files:**
- Modify: `frontend-advisor/src/components/TopbarNav.tsx`
- Modify: `frontend-advisor/src/components/TopbarNav.test.tsx`
- Modify: `frontend-advisor/src/pages/LimitUpPage.tsx`
- Modify: `frontend-advisor/src/pages/LimitUpPage.test.tsx`
- Modify: `frontend-advisor/src/pages/RecommendationsPage.tsx`
- Modify: `frontend-advisor/src/pages/RecommendationsPage.test.tsx`

**Interfaces:**
- Consumes: `sentimentLabel`, `gateShortLabel` / `gateConclusion` from `regimeCopy`
- Topbar: `{ to: '/regime', label: '今日闸门' }`
- LimitUp link text: `查看今日闸门`
- Recommendations: `今日闸门：{gateShortLabel(...)}`；link `查看今日闸门`；drop raw `pool_policy` from badge line (or map shrink→缩池 可选；spec says 不露 pool_policy 原文 → **remove** from badge)

- [ ] **Step 1: Update tests** for new link names and badge text (`今日闸门：风险关闭` or short label from `gateShortLabel('risk_off')` → use **今天先别急着买** OR keep badge shorter「风险关闭」via `gateShortLabel` mapping: risk_off→「风险关闭」for compact badges; page hero uses full conclusion).  

**Decide in code:**  
- Page hero conclusion = `gateConclusion`  
- Badge/history short = `gateShortLabel`: aggressive→可积极；normal→正常；defensive→轻仓观望；risk_off→风险关闭  

- [ ] **Step 2: Implement nav + pages**

- [ ] **Step 3: Run**

```bash
cd frontend-advisor && npm test -- --run \
  src/components/TopbarNav.test.tsx \
  src/pages/LimitUpPage.test.tsx \
  src/pages/RecommendationsPage.test.tsx \
  src/pages/RegimePage.test.tsx \
  src/regimeCopy.test.ts
```

Expected: all PASS

- [ ] **Step 4: Commit**

```bash
git add frontend-advisor/src/components/TopbarNav.tsx frontend-advisor/src/components/TopbarNav.test.tsx \
  frontend-advisor/src/pages/LimitUpPage.tsx frontend-advisor/src/pages/LimitUpPage.test.tsx \
  frontend-advisor/src/pages/RecommendationsPage.tsx frontend-advisor/src/pages/RecommendationsPage.test.tsx
git commit -m "$(cat <<'EOF'
feat(advisor-ui): rename nav to 今日闸门 and sync related copy

EOF
)"
```

---

### Task 4: Manual acceptance tick + optional MonitorJobs string

**Files:**
- Modify (optional light): `frontend-advisor/src/pages/MonitorJobsPage.tsx` — button text「复制今日闸门早盘简报」（spec 未强制；若改动则一并测文案）

- [ ] **Step 1:** Manual checklist against spec acceptance 1–6 (or automate what’s already in tests)
- [ ] **Step 2:** If MonitorJobs string updated, no test required unless trivial; commit with message `chore(advisor-ui): align monitor brief button copy` or skip if out of scope — **skip unless one-line change feels free**
- [ ] **Step 3:** Final test sweep (command in Task 3) + confirm `/regime` still works in browser

---

## Plan self-review

| Spec item | Task |
|-----------|------|
| 今日闸门 title/nav | T3, T2 |
| Decision-first IA | T2 |
| Copy maps + why ≤3 | T1, T2 |
| Folded metrics | T2 |
| LimitUp / Recommendations sync | T3 |
| risk_off override CTA | T2 |
| No backend changes | (all) |

No TBD placeholders. Types: `buildWhyBullets` / `gateConclusion` / `formatCapPct` consistent.

---

## Execution handoff

Plan complete and saved to `docs/superpowers/plans/2026-08-02-regime-page-ux.md`.

**Two execution options:**

1. **Subagent-Driven (recommended)** — fresh subagent per task  
2. **Inline Execution** — this session with executing-plans  

Which approach?
