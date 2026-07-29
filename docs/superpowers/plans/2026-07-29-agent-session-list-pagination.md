# Agent Session List Pagination Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cursor-paginate Agent chat session lists with scroll-up load-more in sidebar and drawer.

**Architecture:** Extend `list_sessions` with `(before, before_id)` keyset pagination and `has_more`; frontend appends older pages on scroll-top and resets to first page after mutate.

**Tech Stack:** FastAPI, MongoDB, React, vitest/pytest

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-29-agent-session-list-pagination-design.md`
- Single-request limit ≤ 50; no global 40-session hard cap on listing
- Message history pagination out of scope

---

### Task 1: Backend cursor list_sessions

**Files:**
- Modify: `backend/app/advisor/agent/chat_store.py`
- Modify: `backend/app/advisor/routes.py`
- Modify: `backend/tests/test_agent_chat_store.py`

- [x] Add failing tests for first page + second page + has_more
- [x] Implement `list_sessions(..., before=, before_id=)` returning `{sessions, has_more}`
- [x] Wire query params on `GET /agent/sessions`
- [x] Run pytest for chat store

### Task 2: Frontend API + scroll load more

**Files:**
- Modify: `frontend-advisor/src/agentApi.ts`
- Modify: `frontend-advisor/src/pages/AgentChatPage.tsx`
- Modify: `frontend-advisor/src/components/AgentConversationDrawer.tsx`
- Modify: `frontend-advisor/src/components/AgentConversationDrawer.test.tsx`
- Modify: `frontend-advisor/src/pages/AgentChatPage.test.tsx` (as needed)
- Modify: `frontend-advisor/src/styles.css` (footer hint only if needed)

- [x] Extend `listAgentSessions` with cursor + `has_more`
- [x] Shared list scroll handler: load older on reach bottom
- [x] Reset to first page after new/delete/send refresh
- [x] Run vitest for drawer / chat page
