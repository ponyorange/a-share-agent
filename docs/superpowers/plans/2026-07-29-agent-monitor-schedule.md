# Agent Monitor Schedule Enhancement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add real schedule activation (`next_run_at`), once/recurring watch + run_at jobs, job logs with polling UI, and countdown to next run.

**Architecture:** Keep `monitor-worker` polling loop; add schedule fields and activation before trading-only evaluation; append-only `agent_monitor_job_logs`; front-end countdown + log drawer.

**Tech Stack:** FastAPI, MongoDB, existing monitor-worker, React (MonitorJobsPage), pytest/vitest

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-29-agent-monitor-schedule-design.md`
- TZ fixed `Asia/Shanghai`; once watch ends at **15:05** local
- `calendar`: `trading_days` | `everyday` (chosen at create for recurring)
- `run_at` executes main Agent once + email; no new cron dependency
- Migrate legacy jobs to `watch` + `recurring` + `trading_days`

---

### Task 1: Schedule helpers + next_run_at

**Files:**
- Create: `backend/app/advisor/monitor/schedule.py`
- Test: `backend/tests/test_monitor_schedule.py`

**Interfaces:**
- Produces:
  - `compute_next_run_at(job: dict, *, now: datetime | None = None) -> datetime | None`
  - `compute_watch_end_at(anchor_date: str, end_time: str = "15:05") -> datetime`
  - `shanghai_hhmm_on(date_str: str, hhmm: str) -> datetime`

- [ ] Write failing tests for once-watch next morning and recurring everyday after fire
- [ ] Implement `schedule.py` using `is_trading_day`
- [ ] Run: `backend/.venv/bin/python -m pytest tests/test_monitor_schedule.py -q`
- [ ] Commit: `feat(monitor): add schedule next_run_at helpers`

---

### Task 2: Models, store create/migrate, job logs

**Files:**
- Modify: `backend/app/advisor/monitor/models.py`
- Modify: `backend/app/advisor/monitor/store.py`
- Create: `backend/app/advisor/monitor/logs.py`
- Test: `backend/tests/test_monitor_store_schedule.py`

**Interfaces:**
- `CreateJobBody` adds `kind`, `repeat`, `calendar`, `anchor_date`, `run_time`, `end_time`, `prompt`
- `create_job` → `status=scheduled`, sets `next_run_at` / `end_at`
- `list_due_scheduled_jobs(now)`, `normalize_legacy_job(doc)`
- `append_job_log(...)`, `list_job_logs(...)`

- [ ] Tests for create once-watch schedule fields and legacy normalize
- [ ] Implement models/store/logs
- [ ] pytest + commit: `feat(monitor): schedule fields on create and job logs`

---

### Task 3: Worker activation, watch finalize, run_at execution

**Files:**
- Modify: `backend/app/advisor/monitor/engine.py`
- Modify: `backend/app/advisor/monitor/worker.py`
- Create: `backend/app/advisor/monitor/run_at.py`
- Test: `backend/tests/test_monitor_engine_schedule.py`

**Tick order:**
1. Activate due `scheduled` jobs
2. Finalize watch windows (once → completed; recurring → scheduled + next)
3. If trading: evaluate **watch + running** only (existing rules/LLM)
4. `run_at` due jobs execute via `execute_run_at_job` (Agent + email)

- [ ] Tests: activate at 09:15; complete once after 15:05
- [ ] Implement activation/finalize + run_at
- [ ] Worker always processes due jobs (not only when is_trading)
- [ ] pytest + commit: `feat(monitor): activate schedules and run_at execution`

---

### Task 4: HTTP API + Agent tools + system prompt

**Files:**
- Modify: `backend/app/advisor/routes.py`
- Modify: `backend/app/advisor/agent/tools.py`
- Modify: `backend/app/advisor/agent/graph.py` (rule 24)
- Tests: monitor/tools tests as needed

- [ ] `GET /monitor/jobs/{id}/logs`; create/list return schedule fields
- [ ] `create_monitor_job` collects kind/repeat/calendar/times; preview shows `next_run_at`
- [ ] SYSTEM_PROMPT rule 24 documents once/recurring/watch/run_at
- [ ] commit: `feat(monitor): API and agent tools for schedules`

---

### Task 5: Frontend countdown + log drawer

**Files:**
- Modify: `frontend-advisor/src/pages/MonitorJobsPage.tsx`
- Modify: `frontend-advisor/src/api.ts` (monitor helpers)
- Modify: `frontend-advisor/src/styles.css`
- Test: `frontend-advisor/src/pages/MonitorJobsPage.test.tsx`

- [ ] Countdown from `next_run_at`; hide when `status===running && kind===watch`
- [ ] Log drawer polls every 3s while open
- [ ] vitest + commit: `feat(monitor): job countdown and log console`

---

### Task 6: README note

- [ ] Document that `monitor-worker` must run for off-hours activation
- [ ] commit if changed

## Spec coverage

| Spec item | Task |
|-----------|------|
| next_run_at activation | 1–3 |
| once watch ends 15:05 | 3 |
| recurring watch | 1, 3 |
| run_at Agent+email | 3 |
| calendar options | 1, 2, 4 |
| logs + poll UI | 2, 5 |
| countdown | 5 |
| legacy migrate | 2 |
| Agent create semantics | 4 |
