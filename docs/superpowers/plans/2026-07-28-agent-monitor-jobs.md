# Agent Monitor Jobs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 一期落地用户级盯盘定时任务：结构化规则 + 交易时段 worker 轮询 + 邮件直发（无 confirm）+ Agent 工具与「定时任务」管理页。

**Architecture:** Mongo `agent_monitor_jobs` 存任务；`monitor/` 模块负责 CRUD、规则求值、邮件；独立 `python -m app.advisor.monitor.worker` 进程交易时段扫描；HTTP `/api/advisor/monitor/*` 与 Agent tools 共用服务层；前端 `/agent/jobs`。

**Tech Stack:** FastAPI、MongoDB、pytest、现有 `get_last_quote` / `trading_session` / `send_email`、React、Docker Compose

## Global Constraints

- 一期只发邮件，不下单；`llm_enabled` 等字段写默认值但不启用
- 盯盘邮件**无需**用户 confirm；须已验证邮箱
- 规则类型仅：`price_below` | `price_above` | `day_chg_below` | `day_chg_above`
- 冷却默认 1800s，键 `symbol:rule_id`
- 每用户最多 20 任务；`scope=symbols` 最多 50 码
- Worker 交易时段约 20–30s 一轮；非交易休眠更长
- 删除硬删；状态仅 `running` | `paused`
- Spec：`docs/superpowers/specs/2026-07-28-agent-monitor-jobs-design.md`
- 计划中的 commit 步骤默认跳过，除非用户明确要求提交

---

### File map

| 文件 | 职责 |
|------|------|
| `backend/app/advisor/monitor/__init__.py` | 包导出 |
| `backend/app/advisor/monitor/models.py` | Pydantic 请求/规则校验 |
| `backend/app/advisor/monitor/store.py` | Mongo CRUD、展开 symbols |
| `backend/app/advisor/monitor/rules.py` | 规则求值纯函数 |
| `backend/app/advisor/monitor/alerts.py` | 组装并发送告警邮件 |
| `backend/app/advisor/monitor/engine.py` | 单 tick：扫 running jobs |
| `backend/app/advisor/monitor/worker.py` | `python -m` 循环入口 |
| `backend/tests/test_monitor_rules.py` | 规则/冷却单测 |
| `backend/tests/test_monitor_store.py` | CRUD/邮箱校验单测 |
| `backend/app/db.py` | 索引 |
| `backend/app/advisor/routes.py` | HTTP |
| `backend/app/advisor/agent/tools.py` | Agent 工具 |
| `backend/app/advisor/agent/graph.py` | SYSTEM_PROMPT |
| `deploy/docker-compose.yml` + `scripts/package-docker.sh` | `monitor-worker` 服务 |
| `frontend-advisor/src/api.ts` | API 客户端 |
| `frontend-advisor/src/pages/MonitorJobsPage.tsx` | 管理页 |
| `frontend-advisor/src/components/TopbarNav.tsx` | Agent 导航 |
| `frontend-advisor/src/components/MobileAgentMoreMenu.tsx` | 移动更多菜单 |
| `frontend-advisor/src/App.tsx` | 路由 |
| `README.md` / `deploy/README.md` | 运维一句说明 |

---

### Task 1: 规则求值 + 冷却纯函数

**Files:**
- Create: `backend/app/advisor/monitor/rules.py`
- Create: `backend/tests/test_monitor_rules.py`

**Interfaces:**
- Produces:
  - `RULE_TYPES = frozenset({...})`
  - `evaluate_rule(rule: dict, quote: dict) -> bool`
  - `cooldown_key(symbol: str, rule_id: str) -> str`
  - `is_cooled_down(cooldowns: dict, key: str, now: datetime, cooldown_sec: int) -> bool`
  - `mark_cooldown(cooldowns: dict, key: str, now: datetime) -> dict`（返回新 dict）

- [ ] **Step 1: 写失败单测**

```python
# backend/tests/test_monitor_rules.py
from datetime import datetime, timedelta, timezone
from app.advisor.monitor.rules import (
    evaluate_rule,
    cooldown_key,
    is_cooled_down,
    mark_cooldown,
)

def test_price_and_chg_rules():
    q = {"price": 10.0, "day_chg_pct": -0.04}
    assert evaluate_rule({"type": "price_below", "value": 10.0}, q) is True
    assert evaluate_rule({"type": "price_above", "value": 10.5}, q) is False
    assert evaluate_rule({"type": "day_chg_below", "value": -0.03}, q) is True
    assert evaluate_rule({"type": "day_chg_above", "value": 0.03}, q) is False

def test_missing_price_skips():
    assert evaluate_rule({"type": "price_below", "value": 1}, {"price": None}) is False

def test_cooldown():
    now = datetime(2026, 7, 28, 10, 0, tzinfo=timezone.utc)
    key = cooldown_key("510300", "r1")
    cds = mark_cooldown({}, key, now)
    assert is_cooled_down(cds, key, now + timedelta(seconds=100), 1800) is False
    assert is_cooled_down(cds, key, now + timedelta(seconds=1801), 1800) is True
```

- [ ] **Step 2: 跑测确认失败**

Run: `cd backend && .venv/bin/python -m pytest tests/test_monitor_rules.py -q`  
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现 `rules.py`**

- [ ] **Step 4: 跑测通过**

Expected: PASS

---

### Task 2: Store CRUD + 邮箱校验

**Files:**
- Create: `backend/app/advisor/monitor/__init__.py`
- Create: `backend/app/advisor/monitor/models.py`
- Create: `backend/app/advisor/monitor/store.py`
- Create: `backend/tests/test_monitor_store.py`
- Modify: `backend/app/db.py`

**Interfaces:**
- Produces:
  - `JOBS_MAX_PER_USER = 20`
  - `SYMBOLS_MAX = 50`
  - `DEFAULT_COOLDOWN_SEC = 1800`
  - `CreateJobBody`（Pydantic）：title, scope, symbols?, rules, note?, cooldown_sec?
  - `list_jobs(user_id) -> list[dict]`（序列化 `_id`→`id`）
  - `create_job(user_id, body) -> dict`（校验邮箱；超限 ValueError）
  - `get_job(user_id, job_id) -> dict | None`
  - `pause_job` / `resume_job` / `delete_job`
  - `resolve_symbols(job: dict) -> list[str]`（读 watchlist/portfolio）
  - `list_running_jobs() -> list[dict]`（worker 用）
  - `touch_job_run(job_id, **fields)`（更新 last_run_at / cooldowns / last_error）

邮箱：复用 tools 内逻辑，抽到 `store` 或 `app/advisor/monitor/store.py` 内：

```python
def require_verified_email(user_id: str) -> str:
    # ObjectId + users.email_verified_at；失败 raise ValueError("请先在个人资料绑定并验证邮箱")
```

`create_job` 写入默认：`llm_enabled=False`, `llm_interval_sec=900`, `llm_anomaly_abs_chg=0.03`, `status=running`, 为每条 rule 生成短 `id`（`uuid4().hex[:8]`）。

索引：

```python
db.agent_monitor_jobs.create_index([("user_id", ASCENDING), ("status", ASCENDING)])
db.agent_monitor_jobs.create_index([("user_id", ASCENDING), ("updated_at", DESCENDING)])
```

- [ ] **Step 1: Fake Mongo 单测**（create 无邮箱失败、上限、pause/resume/delete、resolve_symbols mock load_watchlist）

- [ ] **Step 2: 实现 store/models + 索引**

- [ ] **Step 3: 跑测**

Run: `cd backend && .venv/bin/python -m pytest tests/test_monitor_store.py tests/test_monitor_rules.py -q`  
Expected: PASS

---

### Task 3: 告警邮件 + Engine tick

**Files:**
- Create: `backend/app/advisor/monitor/alerts.py`
- Create: `backend/app/advisor/monitor/engine.py`
- Create: `backend/tests/test_monitor_engine.py`

**Interfaces:**
- `send_monitor_alert(*, to, title, symbol, name, quote, rule, job_id) -> None` → `send_email`
- `run_monitor_tick(*, quote_limit: int = 200) -> dict` 统计 `{jobs, quotes, alerts, errors}`

`run_monitor_tick` 逻辑：

1. `from ..quote import trading_session, get_last_quote`；若非 `is_trading`，return early  
2. `list_running_jobs()`  
3. 对每个 job：`symbols = resolve_symbols(job)[:50]`；逐个 quote（计入全局 quote_limit）  
4. 对每个 rule：`evaluate_rule`；冷却通过则 `send_monitor_alert` + `mark_cooldown`  
5. `touch_job_run` 写回 `last_run_at`、`alert_cooldowns`、`last_alert_at`、`last_error`

单测：monkeypatch `list_running_jobs` / `get_last_quote` / `send_email` / `trading_session`，断言命中发信一次、冷却内不发第二次。

- [ ] **Step 1–4: TDD 实现并跑通**

---

### Task 4: Worker 进程 + Compose

**Files:**
- Create: `backend/app/advisor/monitor/worker.py`
- Modify: `deploy/docker-compose.yml`
- Modify: `scripts/package-docker.sh`（生成 dist compose 时同样加入 monitor-worker）
- Modify: `README.md`、`deploy/README.md`（各加 2–4 行说明）

**Interfaces:**
- `python -m app.advisor.monitor.worker`：死循环调用 `run_monitor_tick`；trading 时 sleep 25s，否则 60s；捕获异常打日志不退出

Compose 服务（镜像同主应用，参考 committee-worker）：

```yaml
  monitor-worker:
    image: share-data:amd64   # 或脚本里的变量标签
    pull_policy: never
    container_name: share-data-monitor-worker
    restart: always
    env_file: .env   # 或显式 MONGODB_URI + MAIL_* 
    environment:
      - MONGODB_URI=${MONGODB_URI:-}
      # MAIL_* 与主应用一致，保证能发信
    command: ["python", "-m", "app.advisor.monitor.worker"]
```

注意：`package-docker.sh` 里嵌入的 compose 模板必须同步，否则发版包缺服务。

- [ ] **Step 1: 实现 worker**
- [ ] **Step 2: 更新 compose / package 模板 / README**
- [ ] **Step 3: 本地冒烟**

Run: `cd backend && .venv/bin/python -c "from app.advisor.monitor.engine import run_monitor_tick; print(run_monitor_tick())"`  
Expected: 打印统计 dict（无 job 时 alerts=0）

---

### Task 5: HTTP 路由

**Files:**
- Modify: `backend/app/advisor/routes.py`

**Interfaces:** 挂在 advisor router：

```text
GET    /monitor/jobs
POST   /monitor/jobs
POST   /monitor/jobs/{job_id}/pause
POST   /monitor/jobs/{job_id}/resume
DELETE /monitor/jobs/{job_id}
```

模式同 portfolio：`Depends(_user)` + `_bind`；`ValueError`→400；找不到→404。

- [ ] **Step 1: 添加路由**
- [ ] **Step 2: 导入检查**

Run: `cd backend && .venv/bin/python -c "from app.advisor.routes import router; print('ok')"`

---

### Task 6: Agent 工具 + SYSTEM_PROMPT

**Files:**
- Modify: `backend/app/advisor/agent/tools.py`
- Modify: `backend/app/advisor/agent/graph.py`

**Tools（JSON 字符串，pause/resume/delete 无 confirm）：**

```python
@tool
def list_monitor_jobs() -> str: ...

@tool
def create_monitor_job(
    title: str,
    scope: str,
    rules_json: str,
    symbols_json: str = "[]",
    note: str = "",
) -> str:
    """创建盯盘任务。scope=watchlist|portfolio|symbols。
    rules_json 为规则数组 JSON。缺邮箱或字段非法返回 ok:false。
    创建前须已与用户确认规则；本工具本身不再二次 confirm。"""

@tool
def pause_monitor_job(job_id: str = "", title: str = "") -> str: ...
@tool
def resume_monitor_job(job_id: str = "", title: str = "") -> str: ...
@tool
def delete_monitor_job(job_id: str = "", title: str = "") -> str: ...
```

解析 `job_id` 或唯一 title 匹配；多匹配返回错误列表。

SYSTEM_PROMPT 新增一条（接在现有规则后）：

```text
24. 盯盘定时任务：创建前问清监控范围（收藏/持仓/指定代码）、触发规则（价或涨跌幅）、任务名；
   复述后用户确认再 create_monitor_job。告警邮件自动发送无需 confirm，且不下单。
   暂停/继续/删除用 pause_monitor_job / resume_monitor_job / delete_monitor_job。
   一期勿启用 LLM 盯盘字段。
```

挂到 `return [...]` 列表（靠近 watchlist 工具）。

- [ ] **Step 1–3: 实现、挂载、静态检查工具名在源码 return 列表中**

---

### Task 7: 前端 API + 管理页 + 导航

**Files:**
- Modify: `frontend-advisor/src/api.ts`
- Create: `frontend-advisor/src/pages/MonitorJobsPage.tsx`
- Modify: `frontend-advisor/src/components/TopbarNav.tsx`（`AGENT_NAV_LINKS` 加 `{ to: '/agent/jobs', label: '定时任务' }`）
- Modify: `frontend-advisor/src/components/MobileAgentMoreMenu.tsx`（同步 AGENT 链接；可改为从 TopbarNav 导入 `AGENT_NAV_LINKS`）
- Modify: `frontend-advisor/src/App.tsx`（Route）
- Optional test: `MonitorJobsPage` smoke 或扩展 `TopbarNav.test` 断言链接存在

**页面要点：**

- 加载 `GET /api/advisor/monitor/jobs`
- 列：标题、范围、状态、规则摘要、最近运行/告警、操作（暂停|继续、删除）
- 空态文案：可在投研助手对话里说「帮我盯…」

- [ ] **Step 1: api.ts 类型与方法**
- [ ] **Step 2: MonitorJobsPage**
- [ ] **Step 3: 导航 + 路由**
- [ ] **Step 4: `npm run build` + 相关 vitest**

---

### Task 8: 端到端验收清单

- [ ] 后端：`pytest tests/test_monitor_*.py -q` PASS  
- [ ] 前端：`npm run build` PASS  
- [ ] 手动（可选）：绑定邮箱 → Agent 创建任务 → 管理页可见 → pause 后 worker 不告警 → resume → delete  

---

## Spec coverage

| Spec | Task |
|------|------|
| 数据模型 / 上限 / 索引 | 2 |
| 规则求值 / 冷却 | 1 |
| 邮件直发 | 3 |
| Worker + compose | 4 |
| HTTP API | 5 |
| Agent tools + prompt | 6 |
| 管理页 + 导航 | 7 |
| LLM / 下单 | 不做（字段默认关） |

## Placeholder scan

无 TBD；commit 可选。
