# Agent Monitor LLM Phase-2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在一期盯盘上增加资金异动即时告警，以及基于知识库的 Agent 看盘（间隔/异动触发、结构化 LLM、仅买/卖发邮件）。

**Architecture:** 扩展现有 `monitor-worker` 双通道——通道 A 规则（价/涨跌/资金）即时邮件；通道 B 预拉上下文 + 单次 LLM JSON，仅 buy/sell 发研判邮件。对话创建时 Agent 可据知识库建议规则并开启 `llm_enabled`。

**Tech Stack:** FastAPI、MongoDB、pytest、`get_last_quote` / `featured_indices_snapshot` / unstructured 新闻、`build_chat_model`、`send_email`、React

## Global Constraints

- 只发邮件，不下单（含模拟盘）
- 规则与看盘**并行**；看盘仅 `buy`/`sell` 发信，`hold` 不发
- 知识：必选知识始终注入；任务 `knowledge_ids` 最多 8
- 资金：相对均值×mult **或** 占比≥value，任一满足；缺数据跳过不误报
- 每用户每 tick 通道 B 最多 1 次；单次最多 10 标的
- LLM：用户 DeepSeek 凭证；无凭证跳过并写 `last_llm_error`
- Spec：`docs/superpowers/specs/2026-07-28-agent-monitor-llm-design.md`
- 计划中的 commit 步骤默认跳过，除非用户明确要求提交

---

### File map

| 文件 | 职责 |
|------|------|
| `backend/app/advisor/monitor/flow.py` | 个股主力净流入快照（当日/近窗均值/成交额/占比） |
| `backend/app/advisor/monitor/rules.py` | 扩展 RULE_TYPES + `evaluate_flow_rule` |
| `backend/app/advisor/monitor/llm_watch.py` | 触发判定、上下文包、LLM 调用、解析 JSON |
| `backend/app/advisor/monitor/alerts.py` | 资金告警文案 + 看盘研判邮件 |
| `backend/app/advisor/monitor/engine.py` | tick 内串通道 A（含 flow）与通道 B |
| `backend/app/advisor/monitor/models.py` | CreateJobBody 扩展字段与 flow 规则 |
| `backend/app/advisor/monitor/store.py` | 落库新字段；llm_enabled 校验凭证 |
| `backend/tests/test_monitor_flow_rules.py` | 资金规则纯函数单测 |
| `backend/tests/test_monitor_llm_watch.py` | 触发/解析/hold 不发信 |
| `backend/tests/test_monitor_store.py` | 扩展创建字段与无 Key 拒绝 |
| `backend/app/advisor/agent/tools.py` | create/list 参数扩展 |
| `backend/app/advisor/agent/graph.py` | 规则 24 扩展 |
| `frontend-advisor/src/api.ts` | MonitorJob 类型扩展 |
| `frontend-advisor/src/pages/MonitorJobsPage.tsx` | 看盘列与资金规则摘要 |

---

### Task 1: 资金快照 + flow 规则纯函数

**Files:**
- Create: `backend/app/advisor/monitor/flow.py`
- Modify: `backend/app/advisor/monitor/rules.py`
- Create: `backend/tests/test_monitor_flow_rules.py`

**Interfaces:**
- Produces:
  - `get_flow_snapshot(symbol: str, *, window_days: int = 5) -> dict`  
    键：`ok`, `symbol`, `net_inflow`（最新一日主力净流入）, `avg_abs_net`（近窗 |净流入| 均值或净流入均值绝对值）, `avg_net_inflow`, `amount`（成交额，可空）, `ratio`（net/amount，可空）, `error`
  - `MEAN_ABS_FLOOR = 1e4`（相对条件：`|avg_net_inflow| < FLOOR` 则相对条件不成立）
  - `RULE_TYPES` 增加 `flow_spike_in`, `flow_spike_out`
  - `evaluate_flow_rule(rule: dict, flow: dict) -> bool`

- [ ] **Step 1: 写失败单测**

```python
# backend/tests/test_monitor_flow_rules.py
from app.advisor.monitor.rules import evaluate_flow_rule

def test_flow_ratio_hit():
    flow = {"ok": True, "net_inflow": 5e7, "avg_net_inflow": 1e7, "amount": 4e8, "ratio": 0.125}
    assert evaluate_flow_rule({"type": "flow_spike_in", "value": 0.10, "mult": 3}, flow) is True

def test_flow_relative_hit():
    flow = {"ok": True, "net_inflow": -9e7, "avg_net_inflow": -2e7, "amount": None, "ratio": None}
    assert evaluate_flow_rule({"type": "flow_spike_out", "value": 0.10, "mult": 3}, flow) is True

def test_flow_missing_skips():
    assert evaluate_flow_rule({"type": "flow_spike_in", "value": 0.1}, {"ok": False}) is False

def test_flow_mean_floor_skips_relative_uses_ratio_only():
    # 均值过小：相对不成立；占比不够 → False
    flow = {"ok": True, "net_inflow": 100.0, "avg_net_inflow": 10.0, "amount": 1e9, "ratio": 0.0001}
    assert evaluate_flow_rule({"type": "flow_spike_in", "value": 0.10, "mult": 3}, flow) is False
```

- [ ] **Step 2: 跑测确认失败**

Run: `cd backend && .venv/bin/python -m pytest tests/test_monitor_flow_rules.py -q`  
Expected: FAIL（`evaluate_flow_rule` 不存在）

- [ ] **Step 3: 实现 `evaluate_flow_rule` + 扩展 `RULE_TYPES`**

```python
# rules.py 要点
FLOW_TYPES = frozenset({"flow_spike_in", "flow_spike_out"})
RULE_TYPES = frozenset({...旧..., *FLOW_TYPES})
MEAN_ABS_FLOOR = 1e4

def evaluate_flow_rule(rule, flow) -> bool:
    if not flow or not flow.get("ok"):
        return False
    rtype = rule.get("type")
    if rtype not in FLOW_TYPES:
        return False
    value = float(rule.get("value") if rule.get("value") is not None else 0.10)
    mult = float(rule.get("mult") if rule.get("mult") is not None else 3.0)
    net = flow.get("net_inflow")
    if net is None:
        return False
    # 方向：in 要求 net>0；out 要求 net<0
    if rtype == "flow_spike_in" and net <= 0:
        return False
    if rtype == "flow_spike_out" and net >= 0:
        return False
    avg = flow.get("avg_net_inflow")
    rel = False
    if avg is not None and abs(float(avg)) >= MEAN_ABS_FLOOR:
        rel = abs(float(net)) >= mult * abs(float(avg))
    ratio = flow.get("ratio")
    pct = False
    if ratio is not None:
        pct = abs(float(ratio)) >= value
    return rel or pct
```

- [ ] **Step 4: 实现 `flow.py` 的 `get_flow_snapshot`**

复用 AKShare `stock_individual_fund_flow`（参考 `market_context.fetch_individual_flow_score` 的列探测）。取最近一行作 `net_inflow`；其前 `window_days` 行均值作 `avg_net_inflow`。成交额列名含「成交额」则算 `ratio=net/amount`。失败返回 `ok=False`。可加进程内短缓存（同交易日、同 symbol）。

- [ ] **Step 5: 跑测通过**

Run: `cd backend && .venv/bin/python -m pytest tests/test_monitor_flow_rules.py tests/test_monitor_rules.py -q`  
Expected: PASS

---

### Task 2: 通道 A 接入资金规则 + 邮件文案

**Files:**
- Modify: `backend/app/advisor/monitor/engine.py`
- Modify: `backend/app/advisor/monitor/alerts.py`
- Modify: `backend/tests/test_monitor_engine.py`

**Interfaces:**
- Consumes: `get_flow_snapshot`, `evaluate_flow_rule`, `send_monitor_alert`
- Produces: engine 对 `flow_spike_*` 拉 flow 并告警；邮件正文含净流入/占比

- [ ] **Step 1: 扩展 engine 规则循环**

对每个 rule：
- 若 `type` 以 `flow_spike_` 开头：`flow = get_flow_snapshot(symbol, window_days=int(rule.get("window_days") or 5))`；`evaluate_flow_rule(rule, flow)`；命中则 `send_monitor_alert(..., quote=quote, rule=rule, flow=flow)`（`flow` 可选参数）。
- 否则保持原 `evaluate_rule(rule, quote)`。

- [ ] **Step 2: 扩展 `send_monitor_alert`**

增加可选 `flow: dict | None = None`；若 rule 为资金类型，主题含「资金异动」，正文打印 `net_inflow` / `ratio`。

- [ ] **Step 3: 单测 monkeypatch**

在 `test_monitor_engine.py` 增加：quote 正常 + flow 命中 → `send_monitor_alert` 调用一次；`evaluate_flow_rule` 用真实函数或 patch `get_flow_snapshot`。

- [ ] **Step 4: 跑测**

Run: `cd backend && .venv/bin/python -m pytest tests/test_monitor_engine.py tests/test_monitor_flow_rules.py -q`  
Expected: PASS

---

### Task 3: 通道 B 纯逻辑（触发 + 解析 + 发信过滤）

**Files:**
- Create: `backend/app/advisor/monitor/llm_watch.py`
- Modify: `backend/app/advisor/monitor/alerts.py`
- Create: `backend/tests/test_monitor_llm_watch.py`

**Interfaces:**
- Produces:
  - `LLM_SYMBOL_LIMIT = 10`
  - `should_run_llm_watch(job: dict, quotes_by_symbol: dict[str, dict], now: datetime) -> tuple[bool, list[str]]`  
    返回 `(是否跑, 优先标的列表≤10)`；条件见 Spec（间隔 / 异动 vs baseline）
  - `build_watch_context(user_id, job, symbols, quotes_by_symbol) -> str`（拼 prompt 文本）
  - `parse_watch_response(text: str) -> dict`（提取 JSON；失败 raise ValueError）
  - `actions_to_notify(parsed: dict) -> list[dict]`（仅 buy/sell）
  - `run_llm_watch(user_id, job, symbols, quotes_by_symbol) -> dict`  
    返回 `{ok, parsed?, error?, notified: int}`；内部调 `build_chat_model` + `send_watch_digest_email`
  - `send_watch_digest_email(*, to, title, job_id, market_note, items: list[dict]) -> None`（合并一封）
  - `llm_cooldown_key(symbol: str) -> str` → `f"llm:{symbol}"`

- [ ] **Step 1: 触发与解析单测（不调真 LLM）**

```python
from datetime import datetime, timedelta, timezone
from app.advisor.monitor.llm_watch import (
    should_run_llm_watch,
    parse_watch_response,
    actions_to_notify,
)

def test_interval_trigger():
    now = datetime(2026, 7, 28, 10, 0, tzinfo=timezone.utc)
    job = {"llm_enabled": True, "llm_interval_sec": 900, "last_llm_at": None, "llm_anomaly_abs_chg": 0.03, "llm_symbol_baselines": {}}
    ok, syms = should_run_llm_watch(job, {"510300": {"day_chg_pct": 0.01}}, now)
    assert ok and "510300" in syms

def test_anomaly_trigger():
    now = datetime(2026, 7, 28, 10, 0, tzinfo=timezone.utc)
    job = {
        "llm_enabled": True,
        "llm_interval_sec": 900,
        "last_llm_at": (now - timedelta(seconds=60)).isoformat(),
        "llm_anomaly_abs_chg": 0.03,
        "llm_symbol_baselines": {"510300": 0.0},
    }
    ok, _ = should_run_llm_watch(job, {"510300": {"day_chg_pct": 0.04}}, now)
    assert ok is True

def test_parse_and_filter_hold():
    raw = '{"symbols":[{"symbol":"510300","action":"hold","confidence":0.5,"rationale":"x","catalysts":[]},{"symbol":"159915","action":"buy","confidence":0.7,"rationale":"y","catalysts":["news"]}],"market_note":"震荡"}'
    parsed = parse_watch_response(raw)
    items = actions_to_notify(parsed)
    assert len(items) == 1 and items[0]["action"] == "buy"
```

- [ ] **Step 2: 实现 `llm_watch.py` 核心纯函数 + `parse_watch_response`**

`parse_watch_response`：允许 markdown code fence；用正则或 `json.loads` 抽第一个 `{...}`。

`build_watch_context`：
- `from ..knowledge import list_raw, format_always_knowledge_section, get_item`（按项目实际 API）
- 大盘：`from app.market import featured_indices_snapshot, get_market`
- 新闻：`from ..agent import unstructured as ustr` → `fetch_stock_news` / `fetch_index_news_sentiment`（限 5 条摘要）
- 资金：对 symbols 调 `get_flow_snapshot`
- 拼成单个 user prompt 字符串；system 固定要求只输出 JSON 契约

- [ ] **Step 3: 实现 `run_llm_watch`（monkeypatch 友好）**

```python
def run_llm_watch(...):
    try:
        from ..agent.llm import build_chat_model
        model = build_chat_model(user_id, temperature=0.2, streaming=False)
        prompt = build_watch_context(...)
        msg = model.invoke([("system", SYSTEM), ("human", prompt)])
        text = getattr(msg, "content", str(msg))
        parsed = parse_watch_response(text)
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "notified": 0}
    items = actions_to_notify(parsed)
    # 对每个 item 检查 llm:symbol 冷却后再发；实现选合并一封
    ...
    return {"ok": True, "parsed": parsed, "notified": n, "error": None}
```

- [ ] **Step 4: `send_watch_digest_email`**

主题：`[看盘] {title} · 建议操作`；正文含 market_note + 各标的 action/rationale/catalysts + 免责「不下单」。

- [ ] **Step 5: 跑测**

Run: `cd backend && .venv/bin/python -m pytest tests/test_monitor_llm_watch.py -q`  
Expected: PASS

另加集成向单测：monkeypatch `build_chat_model` 返回假模型，`send_watch_digest_email` 收集调用——hold-only 不发；buy 发一次。

---

### Task 4: Engine 串入通道 B + baselines 写回

**Files:**
- Modify: `backend/app/advisor/monitor/engine.py`
- Modify: `backend/tests/test_monitor_engine.py`

**Interfaces:**
- Consumes: `should_run_llm_watch`, `run_llm_watch`, `touch_job_run`
- Produces: `run_monitor_tick` 统计增加 `llm_runs` / `llm_notified`（或并入 alerts）

- [ ] **Step 1: 在每个 job 的通道 A 之后**

```python
# 伪代码
quotes_by_symbol = {}  # 通道 A 循环中填充
# ... channel A ...
llm_fields = {}
if job.get("llm_enabled") and user_id not in llm_users_done:
    run, pick = should_run_llm_watch(job, quotes_by_symbol, now)
    if run and pick:
        out = run_llm_watch(job["user_id"], job, pick, quotes_by_symbol)
        llm_users_done.add(job["user_id"])
        llm_fields["last_llm_at"] = now
        # baselines: 用当前 day_chg_pct 覆盖 pick 内标的
        baselines = dict(job.get("llm_symbol_baselines") or {})
        for s in pick:
            chg = (quotes_by_symbol.get(s) or {}).get("day_chg_pct")
            if chg is not None:
                baselines[s] = float(chg)
        llm_fields["llm_symbol_baselines"] = baselines
        llm_fields["last_llm_error"] = out.get("error")
        if out.get("notified"):
            # 更新 llm: 冷却到 alert_cooldowns（在 run_llm_watch 内或此处）
            ...
# touch_job_run 合并 llm_fields
```

注意：同一 tick 内 `llm_users_done` 集合保证每用户最多 1 次通道 B。

- [ ] **Step 2: 单测**

monkeypatch `should_run_llm_watch` → True；`run_llm_watch` → `{ok:True, notified:1, error:None}`；断言 `touch_job_run` 收到 `last_llm_at`。

- [ ] **Step 3: 跑测**

Run: `cd backend && .venv/bin/python -m pytest tests/test_monitor_*.py -q`  
Expected: PASS

---

### Task 5: Store / Models 扩展

**Files:**
- Modify: `backend/app/advisor/monitor/models.py`
- Modify: `backend/app/advisor/monitor/store.py`
- Modify: `backend/tests/test_monitor_store.py`

**Interfaces:**
- `MonitorRuleIn`：`type` 含 flow；可选 `mult: float | None`, `window_days: int | None`
- `CreateJobBody` 增加：
  - `llm_enabled: bool = False`
  - `llm_interval_sec: int | None = None`
  - `llm_anomaly_abs_chg: float | None = None`
  - `knowledge_ids: list[str] = []`
- `rule_to_dict` 写入 `mult`/`window_days`（若有）
- `create_job`：写入上述字段；`knowledge_ids` 去重截断至 8；`llm_enabled=True` 时调用 `resolve_llm_credentials(user_id)`，失败 → `ValueError("请先在 Agent 设置中配置 DeepSeek API Key")`
- 默认：`last_llm_at=None`, `llm_symbol_baselines={}`, `last_llm_error=None`

- [ ] **Step 1: 单测**

```python
def test_create_llm_requires_key(monkeypatch):
    # 有验证邮箱；patch resolve_llm_credentials raise RuntimeError
    with pytest.raises(ValueError, match="DeepSeek"):
        store_mod.create_job(uid, {..., "llm_enabled": True, "rules": [...]})

def test_create_with_flow_and_knowledge(monkeypatch):
    # patch credentials ok
    job = store_mod.create_job(uid, {
        "title": "看盘",
        "scope": "symbols",
        "symbols": ["510300"],
        "rules": [{"type": "flow_spike_in", "value": 0.1, "mult": 3}],
        "llm_enabled": True,
        "knowledge_ids": ["k1", "k1", "k2"],
    })
    assert job["llm_enabled"] is True
    assert job["knowledge_ids"] == ["k1", "k2"]
    assert job["rules"][0]["type"] == "flow_spike_in"
```

- [ ] **Step 2: 实现并跑测**

Run: `cd backend && .venv/bin/python -m pytest tests/test_monitor_store.py -q`  
Expected: PASS

---

### Task 6: Agent 工具 + SYSTEM_PROMPT

**Files:**
- Modify: `backend/app/advisor/agent/tools.py`（`create_monitor_job` / `list_monitor_jobs`）
- Modify: `backend/app/advisor/agent/graph.py`（规则 24）

**Interfaces:**
- `create_monitor_job(..., llm_enabled: bool = False, knowledge_ids_json: str = "[]", llm_interval_sec: int = 900, llm_anomaly_abs_chg: float = 0.03)`
- `list_monitor_jobs` slim 增加 `llm_enabled`, `last_llm_at`, `last_llm_error`

- [ ] **Step 1: 扩展工具签名与 body 透传**

解析 `knowledge_ids_json`；把新字段传入 `create_job`。

- [ ] **Step 2: 替换规则 24 文案**

```text
24. 盯盘定时任务：创建前问清监控范围（收藏/持仓/指定代码）、是否要 Agent 看盘、任务名。
   用户未给涨跌/价格阈值时，先据必选知识（必要时 load_knowledge）建议规则（可含资金异动 flow_spike_in/out），复述确认后再 create_monitor_job。
   开启看盘时设 llm_enabled=true（须已配置 DeepSeek）；说明默认约 15 分钟或涨跌异动约 3% 触发，仅买/卖发邮件、观望不发、不下单。
   规则/资金命中仍即时邮件，与看盘并行。暂停/继续/删除用 pause/resume/delete_monitor_job。
```

- [ ] **Step 3: 静态检查**

Run: `cd backend && .venv/bin/python -c "from app.advisor.agent.graph import SYSTEM_PROMPT; assert 'llm_enabled' in SYSTEM_PROMPT; from app.advisor.agent.tools import build_tools; assert 'create_monitor_job' in [t.name for t in build_tools('u')]"`  
Expected: 无异常

---

### Task 7: 前端管理页

**Files:**
- Modify: `frontend-advisor/src/api.ts`（`MonitorJob` / `MonitorRule` 字段）
- Modify: `frontend-advisor/src/pages/MonitorJobsPage.tsx`

**Interfaces:**
- 类型增加：`llm_enabled?`, `last_llm_at?`, `last_llm_error?`, `knowledge_ids?`；rule `mult?`/`window_days?`；flow 类型文案

- [ ] **Step 1: 更新 RULE_LABEL**

```ts
flow_spike_in: '主力流入异动',
flow_spike_out: '主力流出异动',
```

`formatRule`：flow 类型显示占比%与 mult。

- [ ] **Step 2: 表格列**

增加「看盘」列：开/关；「最近看盘」列：`last_llm_at` + 可选错误一行。

- [ ] **Step 3: 文案**

Hero：规则告警与 Agent 看盘可同时启用；看盘仅买/卖邮件。

- [ ] **Step 4: build**

Run: `cd frontend-advisor && npm run build`  
Expected: PASS

---

### Task 8: 端到端验收

- [ ] **Step 1: 后端全量 monitor 测**

Run: `cd backend && .venv/bin/python -m pytest tests/test_monitor_*.py -q`  
Expected: PASS

- [ ] **Step 2: 前端 build + TopbarNav 测（回归）**

Run: `cd frontend-advisor && npx vitest run src/components/TopbarNav.test.tsx && npm run build`  
Expected: PASS

- [ ] **Step 3: 导入冒烟**

Run: `cd backend && .venv/bin/python -c "from app.advisor.monitor.engine import run_monitor_tick; print(run_monitor_tick())"`  
Expected: 打印含 jobs/quotes/alerts 的 dict

---

## Spec coverage

| Spec 项 | Task |
|---------|------|
| flow_spike 规则与阈值 | 1–2 |
| 通道 A 即时邮件 | 2 |
| 通道 B 触发/上下文/JSON/买卖发信 | 3–4 |
| knowledge_ids + 必选知识 | 3, 5 |
| llm_enabled 校验 Key | 5 |
| API/Store 字段 | 5 |
| Agent 工具与提示（含知识库估阈值流程） | 6 |
| 管理页 | 7 |
| 不下单 / 并行 | 全局 + 3/4 |

## Placeholder scan

无 TBD；commit 可选跳过。

## Type consistency

- `evaluate_flow_rule` / `get_flow_snapshot` / `should_run_llm_watch` / `run_llm_watch` / `send_watch_digest_email` 命名贯穿 Task 1–4。
- Store 字段名与 Spec 一致：`llm_symbol_baselines`, `last_llm_error`, `knowledge_ids`。
