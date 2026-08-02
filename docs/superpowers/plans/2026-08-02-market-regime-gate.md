# Market Regime Gate + Limit-Up Sentiment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship an A-share `MarketRegime` engine (trend pack + full limit-up sentiment), semi-hard gate on recommendations/Agent, REST + advisor UI dashboard, and LimitUp sentiment strip — per `docs/superpowers/specs/2026-08-02-market-regime-gate-design.md`.

**Architecture:** Pure rule engine (`synthesize` / `apply_regime_gate`) over collected metrics; Mongo daily archive for promotion rates; thin FastAPI routes under `/api/advisor/regime/*`; Agent tools + system-prompt rules; `frontend-advisor` nav page `/regime`. LLM never owns the score.

**Tech Stack:** FastAPI, MongoDB, AKShare (via existing `limitup` / `market_context`), pytest, React + React Router + Vitest (`frontend-advisor`)

## Scope note

Spec is large (collector + engine + gate + agent + UI). User locked **方案 3** (single delivery). This plan is one file with ordered, independently testable tasks. Natural pause points if needed: after Task 5 (API green), after Task 7 (Agent), after Task 9 (UI done).

## Global Constraints

- Spec: `docs/superpowers/specs/2026-08-02-market-regime-gate-design.md`
- Docker image tags remain `名称:架构` (e.g. `share-data:amd64`) — unrelated to this feature but repo-wide
- `day_chg_pct` and similar ratios stay decimal (`0.10` = 10%)
- Semi-hard gate: `risk_off` blocks new buys unless `override=true` (then behave as `defensive` + `warnings[]`)
- State from rules+metrics only; LLM explains only
- Do not replace committee `risk_limits`
- Config lives in `backend/app/advisor/config.yaml` under `regime:` (no separate yaml file)
- Advisor frontend entry: top nav 「市场状态」 → `/regime`
- Auth: same as other `/api/advisor/*` routes (`get_current_user`)

## File map

| File | Role |
|------|------|
| Create `backend/app/advisor/regime/__init__.py` | Public exports: `get_current_regime`, `apply_regime_gate`, … |
| Create `backend/app/advisor/regime/types.py` | TypedDict / Literal aliases for regime payload |
| Create `backend/app/advisor/regime/synthesize.py` | Dual-axis → `gate_level` / `position_cap` / `pool_policy` |
| Create `backend/app/advisor/regime/sentiment.py` | Limit-up metrics + cycle from counts + history |
| Create `backend/app/advisor/regime/trend.py` | Trend pack → `trend_regime` |
| Create `backend/app/advisor/regime/store.py` | Mongo `market_regime_daily` read/write |
| Create `backend/app/advisor/regime/collector.py` | Fetch pools + market inputs; build/finalize daily |
| Create `backend/app/advisor/regime/gate.py` | `apply_regime_gate` |
| Create `backend/app/advisor/regime/service.py` | `get_current_regime` / history / sentiment detail |
| Create `backend/tests/test_regime_synthesize.py` | Synthesis table |
| Create `backend/tests/test_regime_sentiment.py` | Sentiment metrics + cycle |
| Create `backend/tests/test_regime_gate.py` | Semi-hard rewrite |
| Create `backend/tests/test_regime_service.py` | Current/history with mocks |
| Create `backend/tests/test_regime_routes.py` | HTTP smoke |
| Modify `backend/app/advisor/config.yaml` | Add `regime:` defaults |
| Modify `backend/app/advisor/service.py` | Gate recommendations; `regime_override` |
| Modify `backend/app/advisor/routes.py` | Regime endpoints + override query on recommendations |
| Modify `backend/app/advisor/snapshots.py` (if snapshot path bypasses gate) | Ensure archived/served recs respect gate or attach regime meta |
| Modify `backend/app/advisor/agent/tools.py` | `get_market_regime`, `get_sentiment_dashboard`; override on recs tool |
| Modify `backend/app/advisor/agent/graph.py` | System prompt rules 25+ for regime |
| Modify `frontend-advisor/src/api.ts` | `fetchRegimeCurrent` / history / sentiment types |
| Create `frontend-advisor/src/pages/RegimePage.tsx` | Dashboard |
| Create `frontend-advisor/src/pages/RegimePage.test.tsx` | Gate level + override CTA |
| Modify `frontend-advisor/src/pages/LimitUpPage.tsx` | Sentiment strip + link |
| Modify `frontend-advisor/src/pages/RecommendationsPage.tsx` | Regime badge + override |
| Modify `frontend-advisor/src/App.tsx` | Route `/regime` |
| Modify `frontend-advisor/src/components/TopbarNav.tsx` | Nav link |
| Modify `frontend-advisor/src/components/TopbarNav.test.tsx` | Assert 「市场状态」 |
| Modify `frontend-advisor` styles (existing css module/global) | Minimal regime layout |

---

### Task 1: Config + synthesize (pure, no I/O)

**Files:**
- Modify: `backend/app/advisor/config.yaml`
- Create: `backend/app/advisor/regime/__init__.py`
- Create: `backend/app/advisor/regime/types.py`
- Create: `backend/app/advisor/regime/synthesize.py`
- Test: `backend/tests/test_regime_synthesize.py`

**Interfaces:**
- Consumes: `load_config()["regime"]`
- Produces:
  - `TrendRegime = Literal["uptrend","range","downtrend"]`
  - `SentimentCycle = Literal["ice","repair","strengthen","climax","ebb"]`
  - `GateLevel = Literal["aggressive","normal","defensive","risk_off"]`
  - `synthesize_gate(trend: TrendRegime, sentiment: SentimentCycle, cfg: dict | None = None) -> dict` with keys `gate_level`, `position_cap`, `pool_policy`

- [ ] **Step 1: Write failing tests**

```python
# backend/tests/test_regime_synthesize.py
from app.advisor.regime.synthesize import synthesize_gate

def test_downtrend_ebb_is_risk_off():
    out = synthesize_gate("downtrend", "ebb", cfg={
        "matrix": {"downtrend": {"ebb": "risk_off"}},
        "position_cap": {"risk_off": 0.15, "defensive": 0.35, "normal": 0.70, "aggressive": 0.85},
        "pool_policy": {"risk_off": "defense_only", "defensive": "shrink", "normal": "full", "aggressive": "full"},
    })
    assert out["gate_level"] == "risk_off"
    assert out["position_cap"] == 0.15
    assert out["pool_policy"] == "defense_only"

def test_uptrend_strengthen_is_aggressive():
    out = synthesize_gate("uptrend", "strengthen", cfg={
        "matrix": {"uptrend": {"strengthen": "aggressive"}},
        "position_cap": {"aggressive": 0.85, "normal": 0.70, "defensive": 0.35, "risk_off": 0.15},
        "pool_policy": {"aggressive": "full", "normal": "full", "defensive": "shrink", "risk_off": "defense_only"},
    })
    assert out["gate_level"] == "aggressive"
```

- [ ] **Step 2: Run test — expect FAIL** (`ModuleNotFoundError` or import error)

```bash
cd backend && .venv/bin/python -m pytest tests/test_regime_synthesize.py -v
```

- [ ] **Step 3: Add `regime:` to `config.yaml`** (append; keep existing keys untouched)

```yaml
regime:
  position_cap:
    aggressive: 0.85
    normal: 0.70
    defensive: 0.35
    risk_off: 0.15
  pool_policy:
    aggressive: full
    normal: full
    defensive: shrink
    risk_off: defense_only
  shrink_top_k: 8
  defensive_buy_threshold_boost: 0.10
  height_board_min: 3
  sentiment_weights:
    seal_rate: 0.25
    height: 0.25
    promotion: 0.25
    limit_up_count: 0.15
    limit_down_penalty: 0.10
  cycle_thresholds:
    ice: 0.20
    repair: 0.35
    strengthen: 0.55
    climax: 0.75
  cycle_hysteresis: 0.05
  matrix:
    uptrend:
      ice: normal
      repair: normal
      strengthen: aggressive
      climax: normal
      ebb: defensive
    range:
      ice: defensive
      repair: defensive
      strengthen: normal
      climax: defensive
      ebb: risk_off
    downtrend:
      ice: risk_off
      repair: risk_off
      strengthen: defensive
      climax: risk_off
      ebb: risk_off
  cache_ttl_seconds: 30
  history_default_limit: 20
```

- [ ] **Step 4: Implement `types.py` + `synthesize.py` + `__init__.py`**

```python
# synthesize.py — look up cfg["matrix"][trend][sentiment]; map caps/policies;
# unknown combo → "defensive" + data note in caller (engine), not here.
def synthesize_gate(trend: str, sentiment: str, cfg: dict | None = None) -> dict:
    from ..config_loader import load_config
    regime = (cfg if cfg is not None else load_config().get("regime") or {})
    matrix = regime.get("matrix") or {}
    level = (matrix.get(trend) or {}).get(sentiment) or "defensive"
    caps = regime.get("position_cap") or {}
    policies = regime.get("pool_policy") or {}
    return {
        "gate_level": level,
        "position_cap": float(caps.get(level, 0.35)),
        "pool_policy": policies.get(level, "shrink"),
    }
```

- [ ] **Step 5: Run tests — expect PASS**

```bash
cd backend && .venv/bin/python -m pytest tests/test_regime_synthesize.py -v
```

- [ ] **Step 6: Commit**

```bash
git add backend/app/advisor/config.yaml backend/app/advisor/regime backend/tests/test_regime_synthesize.py
git commit -m "$(cat <<'EOF'
feat(regime): add config matrix and pure gate synthesizer

EOF
)"
```

---

### Task 2: Sentiment metrics + cycle

**Files:**
- Create: `backend/app/advisor/regime/sentiment.py`
- Test: `backend/tests/test_regime_sentiment.py`

**Interfaces:**
- Consumes: normalized pool summary dicts (not live AKShare in unit tests)
- Produces:
  - `compute_sentiment_metrics(today: dict, prev: dict | None, cfg: dict | None = None) -> dict`  
    Required keys: `limit_up_count`, `limit_down_count`, `broken_count`, `seal_rate`, `break_rate`, `max_board`, `height_board_count`, `promotion_rate` (`None` if `prev` missing), `sentiment_score` (0~1), `sentiment_cycle`, `evidence` (list)
  - `today` shape: `{sealed: list[{board_count:int}], broken: list, limit_down_count: int}`  
  - `prev` shape: `{by_board: dict[int,int]}` counts of sealed by board_count yesterday

- [ ] **Step 1: Failing tests**

```python
from app.advisor.regime.sentiment import compute_sentiment_metrics

CFG = {
    "height_board_min": 3,
    "sentiment_weights": {
        "seal_rate": 0.25, "height": 0.25, "promotion": 0.25,
        "limit_up_count": 0.15, "limit_down_penalty": 0.10,
    },
    "cycle_thresholds": {"ice": 0.20, "repair": 0.35, "strengthen": 0.55, "climax": 0.75},
    "cycle_hysteresis": 0.0,
}

def test_seal_and_break_rates():
    m = compute_sentiment_metrics(
        {"sealed": [{"board_count": 1}] * 8, "broken": [{"board_count": 1}] * 2, "limit_down_count": 1},
        prev=None,
        cfg=CFG,
    )
    assert m["limit_up_count"] == 8
    assert m["broken_count"] == 2
    assert abs(m["seal_rate"] - 0.8) < 1e-6
    assert m["promotion_rate"] is None  # degraded input

def test_promotion_rate_two_day():
    today = {"sealed": [{"board_count": 2}] * 3 + [{"board_count": 1}] * 5, "broken": [], "limit_down_count": 0}
    prev = {"by_board": {1: 10, 2: 2}}  # yesterday 10 first-boards
    m = compute_sentiment_metrics(today, prev=prev, cfg=CFG)
    # 3 boards at 2 / 10 yesterday at 1 → 0.3
    assert abs(m["promotion_rate"] - 0.3) < 1e-6
```

- [ ] **Step 2: Run — expect FAIL**

```bash
cd backend && .venv/bin/python -m pytest tests/test_regime_sentiment.py -v
```

- [ ] **Step 3: Implement `sentiment.py`**

Logic notes (encode in code, not comments-only):
- `seal_rate = sealed / (sealed+broken)` ; if denom 0 → `seal_rate=0`, mark evidence
- `max_board = max(board_count)` on sealed else 0
- `height_board_count = count(board_count >= height_board_min)`
- promotion: for k>=2, sum count_today(k) / max(1, count_prev(k-1)); use primary k=2 rate as `promotion_rate` (spec: 今日 k 连 / 昨 k−1 连 — implement k=2 as main, put higher-k in evidence)
- score: weighted clip 0~1; map to cycle via thresholds (no hysteresis in unit tests when `cycle_hysteresis=0`; production uses previous cycle + hysteresis in `service.py` when applying)

- [ ] **Step 4: Tests PASS**

```bash
cd backend && .venv/bin/python -m pytest tests/test_regime_sentiment.py -v
```

- [ ] **Step 5: Commit**

```bash
git add backend/app/advisor/regime/sentiment.py backend/tests/test_regime_sentiment.py
git commit -m "$(cat <<'EOF'
feat(regime): compute limit-up sentiment metrics and cycle

EOF
)"
```

---

### Task 3: Trend pack

**Files:**
- Create: `backend/app/advisor/regime/trend.py`
- Test: `backend/tests/test_regime_trend.py`

**Interfaces:**
- Consumes: precomputed feature dict (unit tests inject; live path pulls `market_context` + indices)
- Produces: `classify_trend(features: dict, cfg: dict | None = None) -> dict` with `trend_regime`, `evidence`
- `features` keys: `ma_stack` (`above`|`mixed`|`below`), `drawdown_from_high` (0~1), `breadth` (0~1 rise ratio), `volume_vs_ma20` (ratio, 1.0=avg)

- [ ] **Step 1: Failing tests**

```python
from app.advisor.regime.trend import classify_trend

def test_uptrend_when_ma_above_and_breadth_ok():
    out = classify_trend({
        "ma_stack": "above", "drawdown_from_high": 0.05,
        "breadth": 0.62, "volume_vs_ma20": 1.1,
    })
    assert out["trend_regime"] == "uptrend"

def test_downtrend_when_ma_below_and_deep_drawdown():
    out = classify_trend({
        "ma_stack": "below", "drawdown_from_high": 0.22,
        "breadth": 0.30, "volume_vs_ma20": 0.8,
    })
    assert out["trend_regime"] == "downtrend"
```

- [ ] **Step 2: Implement deterministic rules** (document thresholds in `regime.trend_rules` optional; if absent use defaults in code: uptrend if `ma_stack==above` and `breadth>=0.55` and `drawdown_from_high<=0.12`; downtrend if `ma_stack==below` or `drawdown_from_high>=0.18`; else `range`)

- [ ] **Step 3: Tests PASS + commit**

```bash
cd backend && .venv/bin/python -m pytest tests/test_regime_trend.py -v
git add backend/app/advisor/regime/trend.py backend/tests/test_regime_trend.py backend/app/advisor/config.yaml
git commit -m "$(cat <<'EOF'
feat(regime): classify trend regime from breadth and MA features

EOF
)"
```

---

### Task 4: Mongo store + collector facade inputs

**Files:**
- Create: `backend/app/advisor/regime/store.py`
- Create: `backend/app/advisor/regime/collector.py`
- Test: `backend/tests/test_regime_store.py` (mongomock or skip-if-no-db pattern used elsewhere — prefer pure functions + mock `get_db`)

**Interfaces:**
- Produces:
  - `upsert_daily(trade_date: str, doc: dict) -> None`
  - `get_daily(trade_date: str) -> dict | None`
  - `list_daily(limit: int) -> list[dict]`
  - `collect_raw(trade_date: str | None = None) -> dict` returning `{trade_date, sealed, broken, limit_down_count, ladder_max, trend_features, errors: list}`  
    Implementation: reuse `app.limitup` fetch helpers where possible; call `ak.stock_zt_pool_dtgc_em` for limit-down count; build `trend_features` via thin wrappers around existing market index/MA helpers (mock in tests).

- [ ] **Step 1: Test upsert/get roundtrip with monkeypatched db**

```python
def test_upsert_and_get(monkeypatch):
    mem = {}
    class _Col:
        def update_one(self, flt, upd, upsert=False):
            mem[flt["trade_date"]] = {**flt, **upd.get("$set", {})}
        def find_one(self, flt):
            return mem.get(flt.get("trade_date"))
    monkeypatch.setattr("app.advisor.regime.store.get_db", lambda: type("D", (), {"market_regime_daily": _Col()})())
    from app.advisor.regime.store import upsert_daily, get_daily
    upsert_daily("2026-08-01", {"limit_up_count": 40})
    assert get_daily("2026-08-01")["limit_up_count"] == 40
```

- [ ] **Step 2: Implement store (collection `market_regime_daily`, unique `trade_date`)**

- [ ] **Step 3: Implement `collect_raw` with injectable fetch callables for unit test of error aggregation (`errors` non-empty → later `data_quality`)**

- [ ] **Step 4: Tests PASS + commit**

```bash
cd backend && .venv/bin/python -m pytest tests/test_regime_store.py -v
git add backend/app/advisor/regime/store.py backend/app/advisor/regime/collector.py backend/tests/test_regime_store.py
git commit -m "$(cat <<'EOF'
feat(regime): add daily archive store and raw collector

EOF
)"
```

---

### Task 5: `get_current_regime` service + HTTP API

**Files:**
- Create: `backend/app/advisor/regime/service.py`
- Modify: `backend/app/advisor/regime/__init__.py` (export service fns)
- Modify: `backend/app/advisor/routes.py`
- Test: `backend/tests/test_regime_service.py`, `backend/tests/test_regime_routes.py`

**Interfaces:**
- Produces:
  - `build_regime_from_parts(...) -> dict` full `MarketRegime` shape from spec
  - `get_current_regime(*, force: bool = False) -> dict`
  - `get_regime_history(limit: int = 20) -> list[dict]`
  - `get_sentiment_detail() -> dict` (metrics sub-object + cycle)
- HTTP:
  - `GET /api/advisor/regime/current`
  - `GET /api/advisor/regime/history?limit=`
  - `GET /api/advisor/regime/sentiment`
- `data_quality`: `failed` if sealed fetch failed; `degraded` if `promotion_rate is None` or trend features missing; else `ok`. On `failed`, force `gate_level=defensive` via synthesizer override in service (do not invent cycle — set `sentiment_cycle` to last known or `repair` with evidence note).

- [ ] **Step 1: Service unit tests with mocked collect/store**

```python
def test_failed_quality_forces_defensive(monkeypatch):
    # collect_raw returns errors including zt failure → data_quality failed → gate defensive
    ...
```

- [ ] **Step 2: Implement service (TTL cache using `regime.cache_ttl_seconds`; on trading day finalize previous day archive if missing)**

- [ ] **Step 3: Add routes next to other advisor GETs; require `Depends(get_current_user)`**

```python
@router.get("/regime/current")
def regime_current(user=Depends(get_current_user)):
    from .regime import get_current_regime
    return get_current_regime()
```

- [ ] **Step 4: Route test with TestClient + auth fixture used by existing advisor tests**

```bash
cd backend && .venv/bin/python -m pytest tests/test_regime_service.py tests/test_regime_routes.py -v
```

- [ ] **Step 5: Commit**

```bash
git add backend/app/advisor/regime backend/app/advisor/routes.py backend/tests/test_regime_service.py backend/tests/test_regime_routes.py
git commit -m "$(cat <<'EOF'
feat(regime): expose current/history/sentiment advisor APIs

EOF
)"
```

---

### Task 6: Semi-hard `apply_regime_gate` + wire recommendations

**Files:**
- Create: `backend/app/advisor/regime/gate.py`
- Test: `backend/tests/test_regime_gate.py`
- Modify: `backend/app/advisor/service.py` (`get_recommendations`)
- Modify: `backend/app/advisor/routes.py` (query `regime_override: bool = False`)
- Modify: `backend/app/advisor/snapshots.py` and/or `agent/tools.py` `get_today_recommendations` path so served lists also gated ( whichever path the UI uses — trace `snapshot_as_recommendations` and apply gate when returning live/cached items)

**Interfaces:**
- Produces: `apply_regime_gate(result: dict, regime: dict, *, override: bool = False, cfg: dict | None = None) -> dict`  
  Mutates/returns copy of recommendations payload:
  - always sets `result["regime"]` summary (`gate_level`, `position_cap`, `pool_policy`, `data_quality`, `override_applied`)
  - `risk_off` & not override: map buy-like actions (`buy`/`add`/labels worth buying) → `hold`/`watch` per existing action enum in `scoring.decide_action` — inspect actual action strings (`buy` vs watch) and convert **new entry buys** to non-buy; set `gate_blocked_buys=True`
  - override on `risk_off`: treat level as `defensive` for pool ops; append `warnings`
  - `defensive` / shrink: truncate `items` to `shrink_top_k`; optionally raise effective buy threshold by `defensive_buy_threshold_boost` when re-labeling

- [ ] **Step 1: Gate unit tests**

```python
def test_risk_off_blocks_buys():
    regime = {"gate_level": "risk_off", "position_cap": 0.15, "pool_policy": "defense_only", "data_quality": "ok"}
    result = {"items": [{"symbol": "000001", "action": "buy", "score": 0.9}]}
    out = apply_regime_gate(result, regime, override=False)
    assert out["gate_blocked_buys"] is True
    assert out["items"][0]["action"] != "buy"

def test_risk_off_override_warns():
    regime = {"gate_level": "risk_off", "position_cap": 0.15, "pool_policy": "defense_only", "data_quality": "ok"}
    result = {"items": [{"symbol": "000001", "action": "buy", "score": 0.9}] * 20}
    out = apply_regime_gate(result, regime, override=True, cfg={"shrink_top_k": 8, "pool_policy": {"defensive": "shrink"}, "position_cap": {"defensive": 0.35}})
    assert out["regime"]["override_applied"] is True
    assert out.get("warnings")
    assert len(out["items"]) <= 8
```

- [ ] **Step 2: Implement `gate.py`**

- [ ] **Step 3: Wire `get_recommendations(..., regime_override: bool = False)`** — after building items, `regime = get_current_regime()` then `apply_regime_gate(...)`. Same for snapshot serve path used by UI.

- [ ] **Step 4: Extend recommendations route with `regime_override: bool = Query(False)`**

- [ ] **Step 5: Tests PASS + commit**

```bash
cd backend && .venv/bin/python -m pytest tests/test_regime_gate.py tests/test_regime_routes.py -v
git add backend/app/advisor/regime/gate.py backend/app/advisor/service.py backend/app/advisor/routes.py backend/app/advisor/snapshots.py backend/tests/test_regime_gate.py
git commit -m "$(cat <<'EOF'
feat(regime): semi-hard gate on recommendation payloads

EOF
)"
```

---

### Task 7: Agent tools + system prompt

**Files:**
- Modify: `backend/app/advisor/agent/tools.py`
- Modify: `backend/app/advisor/agent/graph.py`
- Test: `backend/tests/test_regime_agent_tools.py` (call tool fns with mocked regime)

**Interfaces:**
- Produces tools:
  - `get_market_regime() -> str` (JSON of `get_current_regime()`)
  - `get_sentiment_dashboard() -> str` (JSON of `get_sentiment_detail()`)
  - `get_today_recommendations(..., regime_override: bool = False)` — add param; pass through
- Register both tools in `build_tools` list
- Append SYSTEM_PROMPT rules (next numbers after 24):

```text
25. 买卖/仓位/今天能否交易：先 get_market_regime；展示 gate_level、position_cap、data_quality、1~3 条 evidence。
26. gate_level=risk_off 且用户未明确要求「仍要看票/强制看推荐」时，不主动推买入名单。
27. 用户明确 override 时：get_today_recommendations(..., regime_override=true) 或等价参数，并复述风险。
28. 打板情绪细节用 get_sentiment_dashboard；指数点位仍用 fetch_market_indices。
```

- [ ] **Step 1: Failing test — tools registered / override flag**

```python
def test_build_tools_includes_regime_tools():
    tools = build_tools("fake-user-id")
    names = {t.name for t in tools}
    assert "get_market_regime" in names
    assert "get_sentiment_dashboard" in names
```

- [ ] **Step 2: Implement tools + prompt**

- [ ] **Step 3: Add default early-brief prompt constant** in `regime/service.py` or `monitor` docs string:

```python
REGIME_MORNING_BRIEF_PROMPT = (
    "请调用 get_market_regime，用中文输出今日市场状态简报："
    "趋势、情绪周期、闸门、仓位上限、三条证据、对交易的含义。"
    "不要编造点位；需要指数点位时再调 fetch_market_indices。"
)
```

Expose via `GET /api/advisor/regime/brief-template` **or** document in MonitorJobs UI helper text — prefer small GET returning `{prompt, suggested_time:"09:05", kind:"run_at"}` so Task 9 can show「一键填入」. Add route in this task.

- [ ] **Step 4: Tests PASS + commit**

```bash
cd backend && .venv/bin/python -m pytest tests/test_regime_agent_tools.py -v
git add backend/app/advisor/agent/tools.py backend/app/advisor/agent/graph.py backend/app/advisor/regime backend/app/advisor/routes.py backend/tests/test_regime_agent_tools.py
git commit -m "$(cat <<'EOF'
feat(regime): agent tools, prompt rules, morning brief template

EOF
)"
```

---

### Task 8: Frontend API + Regime page + nav

**Files:**
- Modify: `frontend-advisor/src/api.ts`
- Create: `frontend-advisor/src/pages/RegimePage.tsx`
- Create: `frontend-advisor/src/pages/RegimePage.test.tsx`
- Modify: `frontend-advisor/src/App.tsx`
- Modify: `frontend-advisor/src/components/TopbarNav.tsx` — insert `{ to: '/regime', label: '市场状态' }` immediately before `{ to: '/limitup', label: '打板' }`
- Modify: `frontend-advisor/src/components/TopbarNav.test.tsx`
- Styles: existing advisor CSS file used by pages (locate via LimitUpPage classnames — same stylesheet)

**Interfaces:**
- `fetchRegimeCurrent(): Promise<RegimeCurrent>`
- `fetchRegimeHistory(limit?: number)`
- `fetchRegimeSentiment()`
- `fetchRegimeBriefTemplate()`

- [ ] **Step 1: Vitest — page shows gate_level and override button when risk_off**

```tsx
it('shows risk_off and override CTA', async () => {
  vi.mocked(api.fetchRegimeCurrent).mockResolvedValue({
    gate_level: 'risk_off',
    position_cap: 0.15,
    trend_regime: 'range',
    sentiment_cycle: 'ebb',
    data_quality: 'ok',
    evidence: [{ key: 'seal_rate', value: '0.4', note: '' }],
    override_allowed: true,
  })
  render(<MemoryRouter><RegimePage /></MemoryRouter>)
  expect(await screen.findByText(/risk_off|风险/i)).toBeInTheDocument()
  expect(screen.getByRole('button', { name: /仍要看今日关注/ })).toBeInTheDocument()
})
```

Override button navigates to `/?regime_override=1` (RecommendationsPage reads it in Task 9) or calls recommendations with flag.

- [ ] **Step 2: Implement page — one hero composition: gate + cap + dual labels; secondary metrics; degraded banner**

- [ ] **Step 3: Wire route + nav; update TopbarNav test for 「市场状态」 → `/regime`**

- [ ] **Step 4: Run**

```bash
cd frontend-advisor && npm test -- --run src/pages/RegimePage.test.tsx src/components/TopbarNav.test.tsx
```

- [ ] **Step 5: Commit**

```bash
git add frontend-advisor/src/api.ts frontend-advisor/src/pages/RegimePage.tsx frontend-advisor/src/pages/RegimePage.test.tsx frontend-advisor/src/App.tsx frontend-advisor/src/components/TopbarNav.tsx frontend-advisor/src/components/TopbarNav.test.tsx
git commit -m "$(cat <<'EOF'
feat(advisor-ui): add market regime dashboard and nav entry

EOF
)"
```

---

### Task 9: LimitUp strip + Recommendations override UX + brief helper

**Files:**
- Modify: `frontend-advisor/src/pages/LimitUpPage.tsx` (+ test)
- Modify: `frontend-advisor/src/pages/RecommendationsPage.tsx` (+ test)
- Modify: `frontend-advisor/src/pages/MonitorJobsPage.tsx` (optional small 「填入市场状态早盘简报」 that loads brief template into create form — if create form is complex, add help link text with prompt copy button only)

- [ ] **Step 1: LimitUp — fetch sentiment on load; show cycle + score; `Link to=/regime`**

- [ ] **Step 2: Recommendations — read `regime_override` query; pass to API; show regime badge from response.`regime`; if `gate_blocked_buys`, show banner + button to enable override

- [ ] **Step 3: Tests + commit**

```bash
cd frontend-advisor && npm test -- --run src/pages/LimitUpPage.test.tsx src/pages/RecommendationsPage.test.tsx
git add frontend-advisor/src/pages/LimitUpPage.tsx frontend-advisor/src/pages/LimitUpPage.test.tsx frontend-advisor/src/pages/RecommendationsPage.tsx frontend-advisor/src/pages/RecommendationsPage.test.tsx frontend-advisor/src/pages/MonitorJobsPage.tsx
git commit -m "$(cat <<'EOF'
feat(advisor-ui): regime strip on limit-up and recommendation override

EOF
)"
```

---

### Task 10: Compatibility mapping + verification sweep

**Files:**
- Modify: `backend/app/advisor/market_context.py` OR thin adapter in `regime/service.py` — `market_score` compatibility: map `gate_level` to 0~1 (`aggressive=0.8`, `normal=0.55`, `defensive=0.35`, `risk_off=0.2`) when enriching context if `regime.use_for_market_score: true` (add flag default `true` in config)
- Test: extend synthesize/service tests
- Manual checklist against spec acceptance table

- [ ] **Step 1: Implement optional mapping behind config flag**

- [ ] **Step 2: Full backend regime tests**

```bash
cd backend && .venv/bin/python -m pytest tests/test_regime_*.py -v
```

- [ ] **Step 3: Frontend targeted tests**

```bash
cd frontend-advisor && npm test -- --run src/pages/RegimePage.test.tsx src/pages/LimitUpPage.test.tsx src/components/TopbarNav.test.tsx
```

- [ ] **Step 4: Spec acceptance tick** (1–9 in design doc) — fix any gap before declaring done

- [ ] **Step 5: Commit**

```bash
git add backend/app/advisor/market_context.py backend/app/advisor/regime backend/app/advisor/config.yaml backend/tests
git commit -m "$(cat <<'EOF'
feat(regime): map gate level into market_score compatibility path

EOF
)"
```

---

## Plan self-review

| Spec requirement | Task |
|------------------|------|
| Dual-axis state machine + matrix | T1 |
| Trend pack | T3 |
| Full limit-up sentiment + promotion via archive | T2, T4 |
| `data_quality` degraded/failed | T5 |
| Semi-hard gate + override | T6 |
| REST current/history/sentiment | T5 |
| Recommendations rewrite | T6 |
| Agent tools + prompt | T7 |
| Morning brief template | T7, T9 |
| Regime dashboard nav | T8 |
| LimitUp strip | T9 |
| Config in `config.yaml` `regime:` | T1 |
| No committee hard-risk replace | (explicit non-goal; no task) |
| `market_score` compatibility | T10 |

Placeholder scan: no TBD/TODO left in steps.  
Type names: `gate_level`, `position_cap`, `pool_policy`, `sentiment_cycle`, `trend_regime`, `apply_regime_gate`, `get_current_regime` consistent across tasks.

---

## Execution handoff

Plan complete and saved to `docs/superpowers/plans/2026-08-02-market-regime-gate.md`.

**Two execution options:**

1. **Subagent-Driven (recommended)** — fresh subagent per task, review between tasks  
2. **Inline Execution** — execute tasks in this session with executing-plans checkpoints  

Which approach?
