# Paper Trader Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有 `monitor-worker` 上落地模拟盘全自动交易员 MVP：双轨决策、硬风控、免确认下单、决策日志与熔断/日终邮件。

**Architecture:** 新包 `backend/app/advisor/paper_trader/` 负责会话、候选/方向打标、LLM 结构化决策、风控闸门与 cycle；`run_monitor_tick` 调用 `run_due_paper_traders()`；HTTP `/api/advisor/paper-trader/*` 与 Agent 工具共用 store；成交 `source="paper_trader"`。

**Tech Stack:** FastAPI、MongoDB、pytest、现有 `place_order` / `get_recommendations` / `load_watchlist` / `get_signal_view` / `resolve_llm_credentials` / `send_email` / `trading_session`

## Global Constraints

- Spec：`docs/superpowers/specs/2026-08-10-paper-trader-agent-design.md`
- 仅模拟盘；对话 Agent 的 `paper_place_order` 仍须 `confirm=true`
- 盯盘 `watch` / `run_at` **不下单**语义不变
- 默认 mode=`signal_first`；`interval_sec` 合法范围 300–900，默认 600
- 默认风控：单票 0.25、总仓 0.90、最多 10 只、日成交 30、日亏 5%、`block_limit_board=true`
- 候选：`recommendations ∪ watchlist`，持仓必进上下文；合计默认上限 40
- 计划中的 commit 步骤默认跳过，除非用户明确要求提交
- Docker 镜像标签仍为 `名称:架构`（如 `share-data:amd64`），本计划不改镜像命名

---

### File map

| 文件 | 职责 |
|------|------|
| `backend/app/advisor/config.yaml` | 新增 `paper_trader:` 默认段 |
| `backend/app/advisor/paper_trader/__init__.py` | 包导出 |
| `backend/app/advisor/paper_trader/defaults.py` | 从 config 读默认 risk/interval |
| `backend/app/advisor/paper_trader/models.py` | Start/Patch/Resume 请求体 |
| `backend/app/advisor/paper_trader/risk.py` | 硬风控纯函数 |
| `backend/app/advisor/paper_trader/candidates.py` | 候选展开 + 买向/卖向打标 |
| `backend/app/advisor/paper_trader/decide.py` | LLM JSON 决策与解析 |
| `backend/app/advisor/paper_trader/store.py` | sessions / decisions CRUD |
| `backend/app/advisor/paper_trader/cycle.py` | 单轮流水线 |
| `backend/app/advisor/paper_trader/mailer.py` | 熔断 + 日终邮件 |
| `backend/app/advisor/paper_trader/scheduler.py` | `run_due_paper_traders` + 日切 |
| `backend/app/db.py` | 索引 |
| `backend/app/advisor/monitor/engine.py` | tick 末尾接入 scheduler |
| `backend/app/advisor/routes.py` | HTTP |
| `backend/app/advisor/agent/tools.py` | Agent 工具 |
| `backend/app/advisor/agent/graph.py` | SYSTEM_PROMPT 一句说明 |
| `README.md` | 运维一句：worker 亦跑交易员 |
| `backend/tests/test_paper_trader_*.py` | 单测 |

---

### Task 1: 配置默认值 + 硬风控纯函数

**Files:**
- Modify: `backend/app/advisor/config.yaml`（文件末尾追加 `paper_trader:`）
- Create: `backend/app/advisor/paper_trader/__init__.py`
- Create: `backend/app/advisor/paper_trader/defaults.py`
- Create: `backend/app/advisor/paper_trader/risk.py`
- Create: `backend/tests/test_paper_trader_risk.py`

**Interfaces:**
- Produces:
  - `default_paper_trader_config() -> dict`
  - `RiskLimits` TypedDict / 普通 dict 键：`max_single_position`, `max_total_exposure`, `max_positions`, `max_trades_per_day`, `max_daily_loss_pct`, `lot_size`, `block_limit_board`
  - `Intent = {symbol, side: "buy"|"sell", qty: float, reason?: str}`
  - `filter_intents(intents, *, account, quotes_by_symbol, risk, trades_today: int, equity_day_open: float | None) -> tuple[list[Intent], list[dict]]`  
    返回 `(allowed, blocked)`；`blocked` 项含 `symbol, side, reason`
  - `should_halt_for_daily_loss(*, equity, equity_day_open, max_daily_loss_pct) -> bool`
  - `is_near_limit_board(quote: dict, *, board: str | None = None) -> bool`

- [ ] **Step 1: 写失败单测**

```python
# backend/tests/test_paper_trader_risk.py
from app.advisor.paper_trader.risk import (
    filter_intents,
    is_near_limit_board,
    should_halt_for_daily_loss,
)

RISK = {
    "max_single_position": 0.25,
    "max_total_exposure": 0.90,
    "max_positions": 10,
    "max_trades_per_day": 30,
    "max_daily_loss_pct": 0.05,
    "lot_size": 100,
    "block_limit_board": True,
}


def test_blocks_over_single_position():
    account = {
        "cash": 50_000,
        "equity": 100_000,
        "positions": [{"symbol": "600000", "qty": 0, "last": 10.0}],
    }
    quotes = {"600000": {"price": 10.0, "day_chg_pct": 0.01}}
    # 买 3000 股 = 30000 > 25% * 100000
    allowed, blocked = filter_intents(
        [{"symbol": "600000", "side": "buy", "qty": 3000}],
        account=account,
        quotes_by_symbol=quotes,
        risk=RISK,
        trades_today=0,
        equity_day_open=100_000,
    )
    assert allowed == []
    assert blocked[0]["reason"] == "max_single_position"


def test_blocks_limit_up_heuristic():
    assert is_near_limit_board({"price": 11.0, "day_chg_pct": 0.096}) is True
    assert is_near_limit_board({"price": 10.0, "day_chg_pct": 0.01}) is False


def test_daily_loss_halt():
    assert should_halt_for_daily_loss(
        equity=94_000, equity_day_open=100_000, max_daily_loss_pct=0.05
    )
    assert not should_halt_for_daily_loss(
        equity=96_000, equity_day_open=100_000, max_daily_loss_pct=0.05
    )
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && python -m pytest tests/test_paper_trader_risk.py -v`  
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现 defaults + risk**

`config.yaml` 追加：

```yaml
paper_trader:
  interval_sec: 600
  candidate_limit: 40
  llm_timeout_sec: 60
  cycle_timeout_sec: 120
  zero_fill_nudge_rounds: 3
  llm_fail_halt_threshold: 5
  max_sessions_per_tick: 3
  risk:
    max_single_position: 0.25
    max_total_exposure: 0.90
    max_positions: 10
    max_trades_per_day: 30
    max_daily_loss_pct: 0.05
    lot_size: 100
    block_limit_board: true
```

`defaults.py`：`default_paper_trader_config()` 从 `load_config()["paper_trader"]` 深拷贝，缺省时用上表字面量。

`risk.py` 实现要点：
- `is_near_limit_board`：`quote.get("limit_up")/limit_down` 真则 True；若有 `limit_up_price`/`limit_down_price` 与 `price`，相对距离 `< 0.005` 则 True；否则主板启发式 `|day_chg_pct| >= 0.095`，若 `board in ("chiNext","star")` 或代码以 `300`/`688` 开头则阈值 `0.195`
- `filter_intents` 顺序：非法 side/qty → limit board → 日笔数 → 买：整手、现金、单票市值/equity、总仓、持仓只数；卖：可卖量（优先 `position.available_qty`，否则 `qty`）
- 模拟「成交后」累计 exposure 时按顺序扣减 cash / 增加 position，避免同轮多笔一起超限

- [ ] **Step 4: 跑通单测**

Run: `cd backend && python -m pytest tests/test_paper_trader_risk.py -v`  
Expected: PASS

- [ ] **Step 5: Commit（默认跳过）**

```bash
git add backend/app/advisor/config.yaml backend/app/advisor/paper_trader backend/tests/test_paper_trader_risk.py
git commit -m "feat: add paper trader risk gate and defaults"
```

---

### Task 2: 会话与决策 store

**Files:**
- Create: `backend/app/advisor/paper_trader/models.py`
- Create: `backend/app/advisor/paper_trader/store.py`
- Modify: `backend/app/db.py`（在 paper 索引附近追加 trader 索引）
- Create: `backend/tests/test_paper_trader_store.py`

**Interfaces:**
- Produces:
  - `StartBody` / `PatchBody` / `ResumeBody`（Pydantic）
  - `get_session(user_id) -> dict | None`
  - `start_session(user_id, body: StartBody | None = None) -> dict`
  - `pause_session(user_id) -> dict`
  - `stop_session(user_id) -> dict`
  - `resume_session(user_id, *, confirm_halt_resume: bool = False) -> dict`
  - `patch_session(user_id, body: PatchBody) -> dict`
  - `list_due_sessions(now: datetime, *, limit: int) -> list[dict]`
  - `touch_session(user_id, **fields) -> dict`
  - `insert_decision(doc: dict) -> dict`（写入 `_id`/`id`）
  - `list_decisions(user_id, *, page=1, page_size=20) -> dict`
  - `get_decision(user_id, decision_id) -> dict | None`
  - 公开文档经 `_public(doc)`：`_id`→`id` 字符串，datetime ISO

- [ ] **Step 1: 写失败单测（可用 mongomock 或现有测试 DB fixture）**

先查看 `backend/tests/test_monitor_store.py` 的 DB fixture 模式并复用同一方式。

```python
def test_start_pause_stop_roundtrip(user_id="u_pt"):
    from app.advisor.paper_trader.store import (
        get_session, start_session, pause_session, stop_session, resume_session,
    )
    s = start_session(user_id)
    assert s["status"] == "running"
    assert s["mode"] == "signal_first"
    assert 300 <= int(s["interval_sec"]) <= 900
    pause_session(user_id)
    assert get_session(user_id)["status"] == "paused"
    stop_session(user_id)
    assert get_session(user_id)["next_run_at"] is None
    # halted resume without confirm
    from app.advisor.paper_trader.store import touch_session
    touch_session(user_id, status="halted", halt_reason="test")
    try:
        resume_session(user_id, confirm_halt_resume=False)
        assert False, "expected error"
    except ValueError as e:
        assert "confirm" in str(e).lower() or "halt" in str(e).lower()
    s2 = resume_session(user_id, confirm_halt_resume=True)
    assert s2["status"] == "running"
    assert s2.get("halt_reason") in (None, "")
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && python -m pytest tests/test_paper_trader_store.py -v`  
Expected: FAIL

- [ ] **Step 3: 实现 store**

`start_session`：若已有文档则更新为 `running`，合并 body 覆盖的 mode/interval/risk；设置 `next_run_at=now`（立即允许首轮）；快照 `notify_email`——若用户有已验证邮箱则写入，否则 `null`（**不**强制邮箱，与盯盘不同）。复用 `monitor.store.require_verified_email` 的只读查邮箱逻辑时可 catch 后置 null。

`db.py` 追加：

```python
db.paper_trader_sessions.create_index("user_id", unique=True)
db.paper_trader_sessions.create_index(
    [("status", ASCENDING), ("next_run_at", ASCENDING)]
)
db.paper_trader_decisions.create_index(
    [("user_id", ASCENDING), ("started_at", DESCENDING)]
)
db.paper_trader_decisions.create_index(
    [("session_id", ASCENDING), ("started_at", DESCENDING)]
)
```

- [ ] **Step 4: 跑通单测**

Run: `cd backend && python -m pytest tests/test_paper_trader_store.py -v`  
Expected: PASS

- [ ] **Step 5: Commit（默认跳过）**

```bash
git add backend/app/advisor/paper_trader backend/app/db.py backend/tests/test_paper_trader_store.py
git commit -m "feat: add paper trader session and decision store"
```

---

### Task 3: 候选池 + 方向打标

**Files:**
- Create: `backend/app/advisor/paper_trader/candidates.py`
- Create: `backend/tests/test_paper_trader_candidates.py`

**Interfaces:**
- Produces:
  - `Direction = Literal["buy", "sell", "neutral"]`
  - `Candidate = {symbol, name?, direction, rule_score?, graph_action?, in_watchlist, in_recommendations, held_qty}`
  - `build_candidates(user_id: str, *, limit: int | None = None) -> list[Candidate]`
  - 内部可注入依赖以便单测：`get_recs`, `get_watch`, `get_paper_positions`, `get_graph`, `get_rule_score`

- [ ] **Step 1: 写失败单测**

```python
from app.advisor.paper_trader.candidates import build_candidates

def test_union_and_direction(monkeypatch):
    import app.advisor.paper_trader.candidates as c

    monkeypatch.setattr(c, "_recommendation_symbols", lambda uid: ["600000", "600001"])
    monkeypatch.setattr(c, "_watchlist_symbols", lambda uid: ["600001", "600002"])
    monkeypatch.setattr(
        c,
        "_paper_positions",
        lambda uid: [{"symbol": "600003", "qty": 100, "name": "持仓票"}],
    )
    monkeypatch.setattr(c, "_rule_score", lambda sym: 0.7 if sym == "600000" else 0.2)
    monkeypatch.setattr(
        c,
        "_graph_action",
        lambda sym: "SELL" if sym == "600002" else "HOLD",
    )
    monkeypatch.setattr(c, "_buy_sell_thresholds", lambda: (0.55, 0.35))

    rows = build_candidates("u1", limit=40)
    by = {r["symbol"]: r for r in rows}
    assert set(by) >= {"600000", "600001", "600002", "600003"}
    assert by["600000"]["direction"] == "buy"   # score 0.7 >= 0.55
    assert by["600002"]["direction"] == "sell"  # graph SELL
    assert by["600003"]["held_qty"] == 100
```

方向规则（与 spec 一致）：
- graph `BUY` 或 score ≥ buy_threshold → `buy`
- graph `SELL` 或 score ≤ sell_threshold → `sell`
- 若同时冲突：优先 graph action；无 graph 则用分数；再否则 `neutral`

- [ ] **Step 2–4: 红灯 → 实现 → 绿灯**

`_recommendation_symbols`：调用 `get_recommendations(user_id=user_id)`，取 `result["items"][*]["symbol"]`。  
`_watchlist_symbols`：调用 `load_watchlist(user_id)`，取 `result["items"][*]["symbol"]`。  
`_paper_positions`：`get_account(user_id, mark_to_market=False)["positions"]`。  
裁剪：持仓优先保留；其余按 `|score-0.5|` 与非 neutral 优先排序后截断到 `limit`。

Run: `cd backend && python -m pytest tests/test_paper_trader_candidates.py -v`

- [ ] **Step 5: Commit（默认跳过）**

```bash
git add backend/app/advisor/paper_trader/candidates.py backend/tests/test_paper_trader_candidates.py
git commit -m "feat: build paper trader candidate universe with directions"
```

---

### Task 4: LLM 结构化决策

**Files:**
- Create: `backend/app/advisor/paper_trader/decide.py`
- Create: `backend/tests/test_paper_trader_decide.py`

**Interfaces:**
- Produces:
  - `parse_decision_response(text: str) -> dict`（含 `actions: list[{symbol,side,qty?,target_weight?,reason}]`）
  - `normalize_actions(actions, *, candidates, mode, equity, quotes) -> list[Intent]`  
    - 丢弃池外；`signal_first` 下非 HOLD 且 direction 不匹配的丢弃或降为 skip  
    - `target_weight` → qty：`floor(equity * w / price / lot) * lot`（lot 默认 100）
  - `run_llm_decide(user_id, *, mode, candidates, account, quotes, nudge: bool) -> dict`  
    返回 `{actions: list[Intent], raw: str, error?: str}`  
    使用 `resolve_llm_credentials(user_id)` + `ChatOpenAI`（与 `monitor/llm_watch.py` 相同凭证路径）；超时读 config `llm_timeout_sec`

- [ ] **Step 1: 写解析/归一化单测（不打真实 LLM）**

```python
from app.advisor.paper_trader.decide import parse_decision_response, normalize_actions

def test_parse_and_filter_signal_first():
    raw = '''{"actions":[
      {"symbol":"600000","side":"buy","qty":100,"reason":"强势"},
      {"symbol":"999999","side":"buy","qty":100,"reason":"池外"},
      {"symbol":"600001","side":"buy","qty":100,"reason":"中性却买"}
    ]}'''
    parsed = parse_decision_response(raw)
    cands = [
        {"symbol": "600000", "direction": "buy"},
        {"symbol": "600001", "direction": "neutral"},
    ]
    intents = normalize_actions(
        parsed["actions"],
        candidates=cands,
        mode="signal_first",
        equity=100_000,
        quotes={"600000": {"price": 10}, "600001": {"price": 10}},
    )
    assert [i["symbol"] for i in intents] == ["600000"]
```

- [ ] **Step 2–4: 实现 parse（可复用 llm_watch 的 fence 剥离）+ normalize + run_llm_decide**

System prompt 固定要求只输出：

```json
{"actions":[{"symbol":"6位","side":"buy|sell|hold","qty":0,"target_weight":null,"reason":"..."}]}
```

`hold` 在 normalize 时丢弃。`nudge=True` 时追加一句「允许对已打方向标的小仓试错，但仍须合理仓位」。

Run: `cd backend && python -m pytest tests/test_paper_trader_decide.py -v`

- [ ] **Step 5: Commit（默认跳过）**

```bash
git add backend/app/advisor/paper_trader/decide.py backend/tests/test_paper_trader_decide.py
git commit -m "feat: add paper trader structured LLM decide path"
```

---

### Task 5: 单轮 cycle + 下单

**Files:**
- Create: `backend/app/advisor/paper_trader/cycle.py`
- Create: `backend/tests/test_paper_trader_cycle.py`

**Interfaces:**
- Produces:
  - `run_paper_trader_cycle(session: dict, *, now: datetime | None = None) -> dict`  
    返回决策摘要（含 `decision_id`, `orders_placed`, `skip_reason`, `halted`）
  - 步骤：日切 stats → build_candidates → quotes → decide → risk → place_order → insert_decision → touch_session

- [ ] **Step 1: 写集成向单测（全部 monkeypatch）**

```python
def test_cycle_places_order_and_logs(monkeypatch):
    import app.advisor.paper_trader.cycle as cycle

    session = {
        "id": "s1",
        "user_id": "u1",
        "status": "running",
        "mode": "signal_first",
        "interval_sec": 600,
        "risk": { /* 同 Task1 RISK */ },
        "stats_today": {"trades": 0, "buys": 0, "sells": 0, "blocked": 0, "llm_calls": 0, "rounds": 0},
        "equity_day_open": 100_000,
        "day_anchor": None,
        "consecutive_zero_fill": 0,
        "consecutive_llm_fail": 0,
    }
    monkeypatch.setattr(cycle, "build_candidates", lambda uid, limit=None: [
        {"symbol": "600000", "direction": "buy", "held_qty": 0, "name": "测"}
    ])
    monkeypatch.setattr(cycle, "_quotes_for", lambda syms: {
        "600000": {"price": 10.0, "name": "测", "day_chg_pct": 0.01}
    })
    monkeypatch.setattr(cycle, "run_llm_decide", lambda **kw: {
        "actions": [{"symbol": "600000", "side": "buy", "qty": 100, "reason": "ok"}],
        "raw": "{}",
    })
    placed = []

    def fake_place(uid, body, **kw):
        assert kw.get("source") == "paper_trader"
        placed.append(body)
        return {"trade": {"_id": "t1", "symbol": body.symbol, "side": body.side,
                          "qty": body.qty, "price": 10.0}, "account": {"cash": 99000}}

    monkeypatch.setattr(cycle, "place_order", fake_place)
    monkeypatch.setattr(cycle, "get_account", lambda uid, **k: {
        "cash": 100000, "equity": 100000, "positions": []
    })
    saved = {}
    monkeypatch.setattr(cycle, "insert_decision", lambda doc: saved.setdefault("d", {**doc, "id": "d1"}) or saved["d"])
    monkeypatch.setattr(cycle, "touch_session", lambda uid, **f: saved.setdefault("touch", f))

    out = cycle.run_paper_trader_cycle(session)
    assert placed and placed[0].qty == 100
    assert saved["d"]["orders_placed"]
    assert saved["touch"]["next_run_at"] is not None
```

另写：`test_cycle_skips_outside_trading`（monkeypatch `trading_session` → `is_trading=False`，不调用 place_order）；`test_halt_on_daily_loss`。

- [ ] **Step 2–4: 实现 cycle**

关键实现细节：
- 非交易时段：写 decision `skip_reason="not_trading"`，**仍**推进 `next_run_at`（避免盘后狂刷）；或由 scheduler 不调用——**采用 scheduler 不调用，cycle 内再卫士检查**
- `place_order(user_id, PaperOrderBody(...), source="paper_trader", external_idempotency_key=f"paper_trader:{run_id}:{symbol}:{side}")`
- 零成交且本轮存在 buy/sell direction 候选：`consecutive_zero_fill += 1`；否则清零
- LLM `error`：`consecutive_llm_fail += 1`，达 `llm_fail_halt_threshold` → `status=halted`
- 日切：上海日历日期变化时重置 `stats_today`、`equity_day_open=当前权益`、`day_anchor`

Run: `cd backend && python -m pytest tests/test_paper_trader_cycle.py -v`

- [ ] **Step 5: Commit（默认跳过）**

```bash
git add backend/app/advisor/paper_trader/cycle.py backend/tests/test_paper_trader_cycle.py
git commit -m "feat: run paper trader cycle with paper orders"
```

---

### Task 6: Scheduler + 邮件 + 接入 monitor tick

**Files:**
- Create: `backend/app/advisor/paper_trader/mailer.py`
- Create: `backend/app/advisor/paper_trader/scheduler.py`
- Modify: `backend/app/advisor/monitor/engine.py`（`run_monitor_tick` 末尾）
- Create: `backend/tests/test_paper_trader_scheduler.py`
- Modify: `README.md`（盯盘 worker 说明句追加「兼跑模拟盘交易员」）

**Interfaces:**
- Produces:
  - `send_halt_email(session, reason: str) -> None`
  - `send_day_end_email(session, summary: dict) -> None`
  - `run_due_paper_traders(*, now=None) -> dict` 统计 `{"due":n,"ran":n,"errors":n,"halted":n,"day_end":n}`
  - `finalize_paper_trader_day_ends(*, now=None) -> int`

- [ ] **Step 1: 写 scheduler 单测**

```python
def test_run_due_invokes_cycle(monkeypatch):
    import app.advisor.paper_trader.scheduler as sch
    monkeypatch.setattr(sch, "list_due_sessions", lambda now, limit: [
        {"user_id": "u1", "status": "running", "id": "s1"}
    ])
    calls = []
    monkeypatch.setattr(sch, "run_paper_trader_cycle", lambda s, now=None: calls.append(s) or {"halted": False})
    monkeypatch.setattr(sch, "trading_is_open", lambda: True)
    stats = sch.run_due_paper_traders()
    assert stats["ran"] == 1 and len(calls) == 1
```

- [ ] **Step 2–4: 实现并接入 engine**

在 `run_monitor_tick` 的 `try` 统计返回前：

```python
try:
    from ..paper_trader.scheduler import (
        finalize_paper_trader_day_ends,
        run_due_paper_traders,
    )
    pt = run_due_paper_traders()
    stats["paper_trader_runs"] = pt.get("ran", 0)
    stats["paper_trader_errors"] = pt.get("errors", 0)
    stats["paper_trader_day_end"] = finalize_paper_trader_day_ends()
except Exception:
    logger.exception("paper trader tick failed")
    stats["paper_trader_errors"] = int(stats.get("paper_trader_errors") or 0) + 1
```

`run_due_paper_traders`：若非交易时段直接返回 zeros（日终函数单独在非交易时段跑）。对每个 due session `try/except`；可用 `concurrent.futures.ThreadPoolExecutor` + `future.result(timeout=cycle_timeout_sec)` 做单轮超时，超时则 `touch_session` 记 error 并推进 `next_run_at`。

熔断邮件：cycle 返回 `halted=True` 时若 `notify_email` 非空则 `send_halt_email`。

日终：查找 `day_anchor==今日` 且尚未 `day_end_sent_for` 的会话（可在 session 上增字段 `day_end_sent_for: str | null`），在 `is_trading_day` 且已收盘后发送并打标。

Run: `cd backend && python -m pytest tests/test_paper_trader_scheduler.py tests/test_monitor_engine.py -v`

- [ ] **Step 5: Commit（默认跳过）**

```bash
git add backend/app/advisor/paper_trader backend/app/advisor/monitor/engine.py README.md backend/tests/test_paper_trader_scheduler.py
git commit -m "feat: schedule paper trader cycles from monitor worker"
```

---

### Task 7: HTTP API + Agent 工具

**Files:**
- Modify: `backend/app/advisor/routes.py`
- Modify: `backend/app/advisor/agent/tools.py`
- Modify: `backend/app/advisor/agent/graph.py`（SYSTEM_PROMPT 增加交易员启停说明）
- Create: `backend/tests/test_paper_trader_routes.py`（若项目有 TestClient 模式则用；否则测 store 包装函数）

**Interfaces:**
- HTTP 前缀 `/api/advisor/paper-trader`（挂在现有 `router` 上）
- Agent 工具名：
  - `get_paper_trader_status`
  - `start_paper_trader(mode?, interval_sec?, confirm=False)`
  - `pause_paper_trader`
  - `stop_paper_trader`
  - `resume_paper_trader(confirm_halt_resume=False, confirm=False)`
  - `list_paper_trader_decisions(limit=20)`

- [ ] **Step 1: 写路由行为测试或薄封装测试**

至少覆盖：未登录 401（若有现成 auth fixture）；`start` 后 GET 返回 running；`stop` 后 status stopped。

- [ ] **Step 2–4: 实现 routes + tools**

Routes 示例：

```python
@router.get("/paper-trader")
def paper_trader_get(user=Depends(_user)):
    from .paper_trader.store import get_session
    return get_session(user["id"]) or {"status": "stopped"}

@router.post("/paper-trader/start")
def paper_trader_start(body: StartBody | None = None, user=Depends(_user)):
    from .paper_trader.store import start_session
    return start_session(user["id"], body)
# pause / stop / resume / PATCH / decisions 同理
```

Tools：`start`/`resume-from-halt` 走 `_need_confirm`；`pause`/`stop` 直接执行。将新工具加入 `build_tools` 列表（靠近 paper_* 工具）。

`graph.py` SYSTEM_PROMPT 增加一条：模拟盘全自动交易员用 `start_paper_trader` 等工具启停；运行中由 worker 自动下单，无需逐笔 confirm；与对话 `paper_place_order` 的 confirm 规则并存。

Run: `cd backend && python -m pytest tests/test_paper_trader_routes.py tests/test_paper_trader_store.py -v`

- [ ] **Step 5: Commit（默认跳过）**

```bash
git add backend/app/advisor/routes.py backend/app/advisor/agent/tools.py backend/app/advisor/agent/graph.py backend/tests/test_paper_trader_routes.py
git commit -m "feat: expose paper trader HTTP API and agent tools"
```

---

### Task 8: 回归清单与全量相关测试

**Files:**
- 无新生产代码（除非发现缺口）
- Modify: 仅当缺口时补测

- [ ] **Step 1: 跑全套 paper_trader + monitor 回归**

Run:

```bash
cd backend && python -m pytest \
  tests/test_paper_trader_risk.py \
  tests/test_paper_trader_store.py \
  tests/test_paper_trader_candidates.py \
  tests/test_paper_trader_decide.py \
  tests/test_paper_trader_cycle.py \
  tests/test_paper_trader_scheduler.py \
  tests/test_paper_trader_routes.py \
  tests/test_monitor_engine.py \
  tests/test_monitor_engine_schedule.py \
  -v
```

Expected: 全部 PASS

- [ ] **Step 2: 手工核对清单（实现者自检，写入 PR/总结）**

- [ ] 对话 `paper_place_order` 无 confirm 仍返回 `needs_confirm`
- [ ] `source=paper_trader` 成交可在 `/paper/trades` 区分
- [ ] `signal_first` 拒池外 / 拒中性乱买
- [ ] `halted` 后不再 due；resume 需 confirm
- [ ] README 已提及 worker 兼跑交易员

- [ ] **Step 3: Commit（默认跳过）**

```bash
git add -u
git commit -m "test: paper trader regression suite green"
```

---

## Spec coverage checklist

| Spec 项 | Task |
|---------|------|
| 会话模型 / 启停状态机 | 2, 7 |
| 硬风控 + 涨跌停启发式 | 1, 5 |
| 候选 ∪ 方向打标 | 3 |
| 双轨 LLM 决策 | 4, 5 |
| 免确认 `source=paper_trader` 下单 | 5 |
| 决策日志 | 2, 5 |
| monitor-worker 调度 + 超时隔离 | 6 |
| 熔断/日终邮件 | 6 |
| HTTP + Agent 工具 | 7 |
| 盯盘不下单 / Agent confirm 回归 | 8 |
| 换手 nudge | 5（`consecutive_zero_fill`） |
| 专用控制台 / 实盘 | 非目标，无 task |

## Self-review notes

- 无 TBD/TODO 占位；推荐/自选均取 `items[*].symbol`。
- Session 增补字段 `consecutive_zero_fill`、`consecutive_llm_fail`、`day_end_sent_for` 与 spec 兼容（spec 允许实现细节）。
- 类型名全程统一：`Intent`、`filter_intents`、`run_paper_trader_cycle`、`run_due_paper_traders`。
