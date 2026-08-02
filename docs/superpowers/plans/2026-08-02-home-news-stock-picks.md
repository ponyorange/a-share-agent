# 首页资讯驱动观察股 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在首页 Agent 解读刷新流水线中增加第二段「选股小 Agent」：基于今日资讯用白名单工具挑出最多 5 只 3–5 日势头观察股，并硬隔离今日关注推荐工具。

**Architecture:** 简报段仍只产 `summary/bullets/sectors`（不再让单次 LLM 瞎编股票）。选股段用 `create_react_agent` + `build_home_news_stock_pick_tools`（白名单过滤）跑有限轮次，解析 JSON `symbols`/`symbols_note` 写入同一 brief。选股失败降级为空列表，简报仍 `ready`。

**Tech Stack:** FastAPI + LangGraph `create_react_agent` + pytest（`backend`）；React + Vitest（`frontend-advisor`）

## Global Constraints

- Spec: `docs/superpowers/specs/2026-08-02-home-news-stock-picks-design.md`
- 禁止挂载/调用 `get_today_recommendations`、`get_recommendation_archive`、`list_recommendation_dates`
- 目标最多 5 只；证据不足可更少；禁止无依据硬凑
- 观察窗口文案：约 3–5 个交易日；非投资建议
- 选股失败 → `symbols=[]` + `symbols_note`，整次仍可 `ready`
- 不引入完整聊天会话存储；默认首页不自动 refresh
- Docker 镜像标签规则与本功能无关

## File map

| File | Role |
|------|------|
| Create `backend/app/advisor/home_news_stock_picks.py` | 工具白名单、选股 Agent、JSON 解析 |
| Create `backend/tests/test_home_news_stock_picks.py` | 白名单 / 解析 / 失败降级 |
| Modify `backend/app/advisor/home_news_brief.py` | 两段流水线；`symbols_note`；简报段不再产股 |
| Modify `backend/tests/test_home_news_brief.py` | 流水线与 public 字段 |
| Modify `frontend-advisor/src/api.ts` | `symbols_note` / `horizon` 类型 |
| Modify `frontend-advisor/src/pages/HomeNewsSection.tsx` | 「资讯驱动观察股」UI + K 线链接 |
| Modify `frontend-advisor/src/pages/HomeNewsSection.test.tsx` | 标题 / 空态 / 列表 |

---

### Task 1: 选股工具白名单 + 解析器

**Files:**
- Create: `backend/app/advisor/home_news_stock_picks.py`
- Create: `backend/tests/test_home_news_stock_picks.py`

**Interfaces:**
- Produces:
  - `STOCK_PICK_ALLOWED_TOOLS: frozenset[str]`
  - `STOCK_PICK_BLOCKED_TOOLS: frozenset[str]`（至少含三个推荐工具名）
  - `build_home_news_stock_pick_tools(user_id: str) -> list` — 仅允许名单内工具；web 工具随用户配置出现但仍须在允许集合内
  - `parse_stock_pick_payload(text: str) -> dict` → `{ "symbols": [...], "symbols_note": str | None }`
  - Symbol shape: `{ "symbol": str, "name": str, "reason": str, "horizon": "3-5d" }`，最多 5；非法 6 位码丢弃

- [ ] **Step 1: Write failing tests**

```python
# backend/tests/test_home_news_stock_picks.py
from __future__ import annotations

import json


def test_stock_pick_tools_exclude_recommendations(monkeypatch):
    from app.advisor import home_news_stock_picks as picks
    from app.advisor.agent import tools as agent_tools

    class FakeTool:
        def __init__(self, name: str):
            self.name = name

    def fake_build(user_id, *, exclude=None):
        names = [
            "get_stock_quotes",
            "get_leaderboard_brief",
            "fetch_stock_news",
            "fetch_symbol_daily_ma",
            "delegate_data_task",
            "run_python_script",
            "register_tool_dataset",
            "get_today_recommendations",
            "get_recommendation_archive",
            "list_recommendation_dates",
            "web_research",
        ]
        # simulate exclude applied upstream
        blocked = set(exclude or [])
        return [FakeTool(n) for n in names if n not in blocked]

    monkeypatch.setattr(agent_tools, "build_tools", fake_build)
    out = picks.build_home_news_stock_pick_tools("u1")
    names = {t.name for t in out}
    assert "get_today_recommendations" not in names
    assert "get_recommendation_archive" not in names
    assert "list_recommendation_dates" not in names
    assert "get_stock_quotes" in names
    assert "delegate_data_task" in names
    assert "web_research" in names


def test_parse_stock_pick_payload_filters_and_caps():
    from app.advisor.home_news_stock_picks import parse_stock_pick_payload

    raw = json.dumps(
        {
            "symbols": [
                {"symbol": "600519", "name": "贵州茅台", "reason": "联播提消费"},
                {"symbol": "ABC", "name": "坏码", "reason": "x"},
                {"symbol": "000001", "name": "平安银行", "reason": "金融政策"},
                {"symbol": "000002", "name": "万科A", "reason": "地产"},
                {"symbol": "000003", "name": "三", "reason": "r"},
                {"symbol": "000004", "name": "四", "reason": "r"},
                {"symbol": "000005", "name": "五", "reason": "r"},
                {"symbol": "000006", "name": "六", "reason": "应被截断"},
            ],
            "symbols_note": "",
        },
        ensure_ascii=False,
    )
    out = parse_stock_pick_payload(raw)
    assert len(out["symbols"]) == 5
    assert out["symbols"][0]["symbol"] == "600519"
    assert out["symbols"][0]["horizon"] == "3-5d"
    assert all(x["symbol"] != "ABC" for x in out["symbols"])


def test_parse_stock_pick_empty_note():
    from app.advisor.home_news_stock_picks import parse_stock_pick_payload

    out = parse_stock_pick_payload('{"symbols":[],"symbols_note":"证据不足"}')
    assert out["symbols"] == []
    assert out["symbols_note"] == "证据不足"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && .venv/bin/python -m pytest -q tests/test_home_news_stock_picks.py`
Expected: FAIL（模块不存在）

- [ ] **Step 3: Implement `home_news_stock_picks.py`（本任务只实现 tools + parse；Agent runner 放 Task 2）**

```python
# backend/app/advisor/home_news_stock_picks.py
"""News-driven stock picks for home Agent brief (no 今日关注 tools)."""

from __future__ import annotations

import json
import re
from typing import Any

from .agent.tools import build_tools

STOCK_PICK_BLOCKED_TOOLS: frozenset[str] = frozenset(
    {
        "get_today_recommendations",
        "get_recommendation_archive",
        "list_recommendation_dates",
    }
)

STOCK_PICK_ALLOWED_TOOLS: frozenset[str] = frozenset(
    {
        "get_stock_quotes",
        "get_leaderboard_brief",
        "fetch_stock_news",
        "fetch_symbol_daily_ma",
        "delegate_data_task",
        "register_tool_dataset",
        "run_python_script",
        "web_research",
        "web_search",
        "fetch_url",
    }
)


def build_home_news_stock_pick_tools(user_id: str) -> list[Any]:
    """Whitelist tools for stock-pick agent; always block recommendation tools."""
    raw = build_tools(user_id, exclude=STOCK_PICK_BLOCKED_TOOLS)
    return [
        t
        for t in raw
        if getattr(t, "name", None) in STOCK_PICK_ALLOWED_TOOLS
    ]


def parse_stock_pick_payload(text: str) -> dict[str, Any]:
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:].strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            try:
                data = json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                data = {}
        else:
            data = {}
    if not isinstance(data, dict):
        data = {}
    symbols: list[dict[str, str]] = []
    for x in data.get("symbols") or []:
        if not isinstance(x, dict):
            continue
        sym = re.sub(r"\D", "", str(x.get("symbol") or ""))[-6:]
        if not re.fullmatch(r"\d{6}", sym):
            continue
        reason = str(x.get("reason") or "").strip()
        if not reason:
            continue
        symbols.append(
            {
                "symbol": sym,
                "name": str(x.get("name") or "")[:40],
                "reason": reason[:120],
                "horizon": "3-5d",
            }
        )
        if len(symbols) >= 5:
            break
    note = str(data.get("symbols_note") or "").strip()[:200] or None
    if not symbols and not note:
        note = "暂无足够证据的观察股"
    return {"symbols": symbols, "symbols_note": note}
```

- [ ] **Step 4: Run tests**

Run: `cd backend && .venv/bin/python -m pytest -q tests/test_home_news_stock_picks.py`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/advisor/home_news_stock_picks.py backend/tests/test_home_news_stock_picks.py
git commit -m "$(cat <<'EOF'
feat: add home news stock-pick tool allowlist and parser

EOF
)"
```

---

### Task 2: 选股 ReAct Agent runner

**Files:**
- Modify: `backend/app/advisor/home_news_stock_picks.py`
- Modify: `backend/tests/test_home_news_stock_picks.py`

**Interfaces:**
- Consumes: `build_home_news_stock_pick_tools`, `parse_stock_pick_payload`, `build_chat_model`
- Produces:
  - `run_home_news_stock_picks(user_id: str, *, news: dict, sectors: list[dict]) -> dict`
  - 返回 `{ "symbols": [...], "symbols_note": str | None }`
  - 超时/异常 → 空列表 + `symbols_note` 说明，**不抛给调用方**
  - Agent：`create_react_agent`；`config={"recursion_limit": 16}`；`build_chat_model(..., temperature=0.2, streaming=False, request_timeout=90)`

- [ ] **Step 1: Write failing tests**

```python
def test_run_stock_picks_uses_agent_and_parses(monkeypatch):
    from app.advisor import home_news_stock_picks as picks

    monkeypatch.setattr(picks, "build_home_news_stock_pick_tools", lambda uid: [])
    monkeypatch.setattr(picks, "build_chat_model", lambda *a, **k: object())

    class FakeAgent:
        def invoke(self, payload, config=None):
            assert config and config.get("recursion_limit") == 16
            return {
                "messages": [
                    type(
                        "M",
                        (),
                        {
                            "content": json.dumps(
                                {
                                    "symbols": [
                                        {
                                            "symbol": "600519",
                                            "name": "贵州茅台",
                                            "reason": "消费政策预期",
                                        }
                                    ],
                                    "symbols_note": "",
                                },
                                ensure_ascii=False,
                            )
                        },
                    )()
                ]
            }

    monkeypatch.setattr(
        picks,
        "create_react_agent",
        lambda model, tools, prompt=None: FakeAgent(),
    )
    out = picks.run_home_news_stock_picks(
        "u1",
        news={"trade_date": "2026-08-01", "groups": {}},
        sectors=[{"name": "白酒", "reason": "政策"}],
    )
    assert out["symbols"][0]["symbol"] == "600519"


def test_run_stock_picks_degrades_on_error(monkeypatch):
    from app.advisor import home_news_stock_picks as picks

    monkeypatch.setattr(picks, "build_home_news_stock_pick_tools", lambda uid: [])
    monkeypatch.setattr(
        picks, "build_chat_model", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    out = picks.run_home_news_stock_picks("u1", news={}, sectors=[])
    assert out["symbols"] == []
    assert out["symbols_note"]
```

- [ ] **Step 2: Run to verify fail**

Run: `cd backend && .venv/bin/python -m pytest -q tests/test_home_news_stock_picks.py::test_run_stock_picks_uses_agent_and_parses tests/test_home_news_stock_picks.py::test_run_stock_picks_degrades_on_error`
Expected: FAIL（`run_home_news_stock_picks` 未定义）

- [ ] **Step 3: Implement runner**

在 `home_news_stock_picks.py` 追加：

```python
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.prebuilt import create_react_agent

from .agent.llm import build_chat_model

STOCK_PICK_SYSTEM = (
    "你是投研助手，任务：根据「今日资讯」与相关板块，用工具核实后挑选未来约 3–5 个交易日"
    "可能有势头的 A 股观察标的。禁止使用或提及「今日关注」推荐列表。"
    "目标最多 5 只；证据不足可更少，禁止无依据硬凑。"
    "可用工具查成分股、涨幅榜、报价、个股新闻、联网（若已挂载）。"
    "最终只输出 JSON（不要 Markdown 围栏）："
    '{"symbols":[{"symbol":"600000","name":"...","reason":"须点明资讯/题材关联"}],'
    '"symbols_note":"可选说明"}。'
    "reason≤80字；勿保证收益；表述为研究观察。"
)


def _message_text(msg: Any) -> str:
    content = getattr(msg, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text") or ""))
            else:
                parts.append(str(block))
        return "".join(parts)
    return str(content or msg or "")


def _truncate_news(news: dict[str, Any]) -> dict[str, Any]:
    groups = {}
    for k, g in (news.get("groups") or {}).items():
        items = []
        for it in (g.get("items") or [])[:6]:
            if not isinstance(it, dict):
                continue
            items.append(
                {
                    "title": str(it.get("title") or "")[:100],
                    "summary": (str(it.get("summary") or "")[:120] or None),
                }
            )
        groups[k] = {"ok": bool(g.get("ok")), "items": items}
    return {"trade_date": news.get("trade_date"), "groups": groups}


def run_home_news_stock_picks(
    user_id: str,
    *,
    news: dict[str, Any],
    sectors: list[dict[str, Any]],
) -> dict[str, Any]:
    try:
        tools = build_home_news_stock_pick_tools(user_id)
        model = build_chat_model(
            user_id, temperature=0.2, streaming=False, request_timeout=90
        )
        agent = create_react_agent(model, tools, prompt=STOCK_PICK_SYSTEM)
        payload = {
            "news": _truncate_news(news),
            "sectors": sectors[:8],
            "instruction": "请调用工具核实后输出观察股 JSON。",
        }
        result = agent.invoke(
            {
                "messages": [
                    HumanMessage(
                        content=json.dumps(payload, ensure_ascii=False, default=str)
                    )
                ]
            },
            config={"recursion_limit": 16},
        )
        messages = result.get("messages") if isinstance(result, dict) else None
        text = ""
        if messages:
            text = _message_text(messages[-1])
        return parse_stock_pick_payload(text)
    except Exception as exc:  # noqa: BLE001
        return {
            "symbols": [],
            "symbols_note": f"观察股生成失败：{type(exc).__name__}",
        }
```

- [ ] **Step 4: Run tests**

Run: `cd backend && .venv/bin/python -m pytest -q tests/test_home_news_stock_picks.py`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/advisor/home_news_stock_picks.py backend/tests/test_home_news_stock_picks.py
git commit -m "$(cat <<'EOF'
feat: run home news stock-pick react agent with soft failure

EOF
)"
```

---

### Task 3: 接入简报两段流水线

**Files:**
- Modify: `backend/app/advisor/home_news_brief.py`
- Modify: `backend/tests/test_home_news_brief.py`

**Interfaces:**
- Consumes: `run_home_news_stock_picks`
- Changes:
  - `_idle` / `_public` 增加 `symbols_note: str | None`；`symbols` 含可选 `horizon`，上限改为 5
  - `_parse_llm_json` **忽略** LLM 返回的 symbols（简报段不再采用），或从 system prompt 删除 symbols 字段
  - `generate_home_news_brief`：先简报 LLM → 再 `run_home_news_stock_picks` → 合并返回
  - `_spawn_refresh_thread` 保存 `symbols_note`

- [ ] **Step 1: Write / update failing tests**

在 `test_home_news_brief.py` 增加：

```python
def test_generate_brief_runs_stock_picks_after_summary(monkeypatch):
    from app.advisor import home_news_brief as hb

    class FakeModel:
        def invoke(self, messages):
            class R:
                content = json.dumps(
                    {
                        "summary": "政策偏暖",
                        "bullets": ["要点"],
                        "sectors": [{"name": "人工智能", "reason": "题材"}],
                        "symbols": [{"symbol": "999999", "name": "应忽略", "reason": "x"}],
                    },
                    ensure_ascii=False,
                )

            return R()

    monkeypatch.setattr(hb, "resolve_llm_credentials", lambda uid: {"api_key": "x"})
    monkeypatch.setattr(hb, "build_chat_model", lambda uid, **k: FakeModel())
    monkeypatch.setattr(hb, "_optional_knowledge_titles", lambda uid: [])
    monkeypatch.setattr(hb, "_maybe_fetch_web_items", lambda uid: [])
    monkeypatch.setattr(
        hb,
        "run_home_news_stock_picks",
        lambda uid, news, sectors: {
            "symbols": [
                {
                    "symbol": "600519",
                    "name": "贵州茅台",
                    "reason": "消费预期",
                    "horizon": "3-5d",
                }
            ],
            "symbols_note": None,
        },
    )
    news = {
        "trade_date": "2026-08-01",
        "as_of": "t0",
        "groups": {
            "cctv": {"ok": True, "source": "c", "error": None, "items": []},
            "macro": {"ok": True, "source": "m", "error": None, "items": []},
            "index_sentiment": {"ok": False, "source": None, "error": "x", "items": []},
            "sectors": {"ok": True, "source": "s", "error": None, "items": []},
            "web": {"ok": False, "source": None, "error": None, "items": []},
        },
    }
    out = hb.generate_home_news_brief("u1", news)
    assert out["summary"] == "政策偏暖"
    assert out["symbols"][0]["symbol"] == "600519"
    assert out["symbols"][0]["symbol"] != "999999"


def test_public_includes_symbols_note(monkeypatch):
    from app.advisor import home_news_brief as hb

    monkeypatch.setattr(hb, "last_trading_day", lambda: "2026-08-01")
    monkeypatch.setattr(
        hb,
        "_load_brief",
        lambda uid, day: {
            "trade_date": "2026-08-01",
            "status": "ready",
            "summary": "s",
            "bullets": [],
            "sectors": [],
            "symbols": [],
            "symbols_note": "暂无足够证据的观察股",
            "updated_at": "t",
            "error": None,
            "news_as_of": "t0",
        },
    )
    out = hb.get_home_news_brief("u1")
    assert out["symbols_note"] == "暂无足够证据的观察股"
```

同步修改既有 `test_generate_brief_parses_llm_json`：mock `run_home_news_stock_picks` 返回空或固定列表，避免真实 Agent。

- [ ] **Step 2: Run to verify fail**

Run: `cd backend && .venv/bin/python -m pytest -q tests/test_home_news_brief.py -k stock_picks`
Expected: FAIL

- [ ] **Step 3: Wire `home_news_brief.py`**

关键改动要点：

1. import：`from .home_news_stock_picks import run_home_news_stock_picks`
2. `_idle` / `_public` 增加 `symbols_note`
3. `_public` 的 symbols 解析保留 `horizon`（缺省 `3-5d`），`[:5]`
4. 简报 system prompt 改为**不要** `symbols` 字段，只出 summary/bullets/sectors
5. `_parse_llm_json` 返回的 `symbols` 固定 `[]`（或删除该键，由后续覆盖）
6. `generate_home_news_brief` 末尾：

```python
    brief = _parse_llm_json(text)
    picks = run_home_news_stock_picks(
        user_id,
        news=news,
        sectors=brief.get("sectors") or [],
    )
    brief["symbols"] = picks.get("symbols") or []
    brief["symbols_note"] = picks.get("symbols_note")
    return brief
```

7. `_spawn_refresh_thread` 的 `_save_brief` 写入 `symbols_note`

- [ ] **Step 4: Run tests**

Run: `cd backend && .venv/bin/python -m pytest -q tests/test_home_news_brief.py tests/test_home_news_stock_picks.py`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/advisor/home_news_brief.py backend/tests/test_home_news_brief.py
git commit -m "$(cat <<'EOF'
feat: two-stage home brief with news-driven stock picks

EOF
)"
```

---

### Task 4: 前端「资讯驱动观察股」展示

**Files:**
- Modify: `frontend-advisor/src/api.ts`（`HomeNewsBrief` 类型）
- Modify: `frontend-advisor/src/pages/HomeNewsSection.tsx`
- Modify: `frontend-advisor/src/pages/HomeNewsSection.test.tsx`
- Modify: `frontend-advisor/src/styles.css`（若需小标题样式；可复用现有）

**Interfaces:**
- `HomeNewsBrief.symbols_note?: string | null`
- `symbols[].horizon?: string`
- UI 标题「资讯驱动观察股」；副文案固定；空列表显示 note；有票时代码链 `explorerKlineUrl`

- [ ] **Step 1: Update tests**

在 `HomeNewsSection.test.tsx` 追加/调整：

```tsx
  it('shows news-driven stock picks section when ready', async () => {
    vi.mocked(api.fetchHomeNews).mockResolvedValue({
      trade_date: '2026-08-01',
      as_of: 't',
      groups: emptyGroups,
    })
    vi.mocked(api.fetchHomeNewsBrief).mockResolvedValue({
      trade_date: '2026-08-01',
      status: 'ready',
      summary: '政策偏暖',
      bullets: [],
      sectors: [],
      symbols: [
        { symbol: '600519', name: '贵州茅台', reason: '消费预期', horizon: '3-5d' },
      ],
      symbols_note: null,
    })
    render(<HomeNewsSection />)
    await waitFor(() => expect(screen.getByText('资讯驱动观察股')).toBeInTheDocument())
    expect(screen.getByText(/观察窗口约 3–5 个交易日/)).toBeInTheDocument()
    expect(screen.getByText(/600519/)).toBeInTheDocument()
    expect(screen.queryByText('今日关注')).not.toBeInTheDocument()
  })

  it('shows empty note when no symbols', async () => {
    vi.mocked(api.fetchHomeNews).mockResolvedValue({
      trade_date: '2026-08-01',
      as_of: 't',
      groups: emptyGroups,
    })
    vi.mocked(api.fetchHomeNewsBrief).mockResolvedValue({
      trade_date: '2026-08-01',
      status: 'ready',
      summary: '观望',
      bullets: [],
      sectors: [],
      symbols: [],
      symbols_note: '暂无足够证据的观察股',
    })
    render(<HomeNewsSection />)
    await waitFor(() =>
      expect(screen.getByText('暂无足够证据的观察股')).toBeInTheDocument(),
    )
  })
```

- [ ] **Step 2: Run to verify fail**

Run: `cd frontend-advisor && npm test -- --run src/pages/HomeNewsSection.test.tsx`
Expected: FAIL（文案不存在）

- [ ] **Step 3: Update types + UI**

`api.ts`：

```ts
export type HomeNewsBrief = {
  trade_date: string
  status: HomeNewsBriefStatus
  summary: string
  bullets: string[]
  sectors: { name: string; reason: string }[]
  symbols: { symbol: string; name: string; reason: string; horizon?: string }[]
  symbols_note?: string | null
  updated_at?: string | null
  error?: string | null
  news_as_of?: string | null
}
```

`HomeNewsSection.tsx` 在 sectors 之后替换 symbols 块为：

```tsx
import { explorerKlineUrl } from '../explorerLinks'
// ...
{status === 'ready' && brief ? (
  <>
    {/* existing summary/bullets/sectors */}
    <div className="home-news-picks">
      <h4 className="home-news-picks-title">资讯驱动观察股</h4>
      <p className="meta-line">
        基于今日资讯 · 观察窗口约 3–5 个交易日 · 非投资建议
      </p>
      {brief.symbols.length ? (
        <ul className="home-news-symbols">
          {brief.symbols.map((s) => (
            <li key={s.symbol}>
              <a
                className="text-link mono"
                href={explorerKlineUrl(s.symbol)}
                target="_blank"
                rel="noreferrer"
              >
                {s.symbol} {s.name}
              </a>
              <span className="muted">{s.reason}</span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="muted">
          {brief.symbols_note || '暂无足够证据的观察股'}
        </p>
      )}
    </div>
  </>
) : null}
```

（保持原有 summary/bullets/sectors 结构，仅把 symbols 段换成带标题的 picks 块。）

可选 CSS：

```css
.home-news-picks {
  margin-top: 0.75rem;
}
.home-news-picks-title {
  margin: 0 0 0.25rem;
  font-size: 0.9rem;
}
```

- [ ] **Step 4: Run frontend tests**

Run: `cd frontend-advisor && npm test -- --run src/pages/HomeNewsSection.test.tsx src/pages/HomePage.test.tsx src/api.home.test.ts`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend-advisor/src/api.ts \
  frontend-advisor/src/pages/HomeNewsSection.tsx \
  frontend-advisor/src/pages/HomeNewsSection.test.tsx \
  frontend-advisor/src/styles.css
git commit -m "$(cat <<'EOF'
feat: show news-driven observation stocks on home brief

EOF
)"
```

---

### Task 5: 回归核对

- [ ] **Step 1: 跑全套相关测试**

```bash
cd backend && .venv/bin/python -m pytest -q tests/test_home_news.py tests/test_home_news_brief.py tests/test_home_news_stock_picks.py
cd frontend-advisor && npm test -- --run src/api.home.test.ts src/pages/HomeNewsSection.test.tsx src/pages/HomePage.test.tsx
```

Expected: 全部 PASS

- [ ] **Step 2: 手工冒烟（有 DeepSeek Key 时）**  
打开 `/` → 刷新解读 → 右栏出现「资讯驱动观察股」；网络面板无今日关注 API；可选确认工具轨迹不含推荐工具名。

- [ ] **Step 3: 若有小修则单独 commit；否则结束**

---

## Self-review (plan vs spec)

| Spec 要求 | Task |
|-----------|------|
| 两段流水线：简报 → 选股 Agent | Task 2–3 |
| 工具白名单 + 硬排除今日关注 | Task 1 |
| ≤5、可更少、symbols_note | Task 1 parse + Task 3–4 |
| 选股失败仍 ready | Task 2 degrade + Task 3 |
| 前端标题/副文案/空态/禁今日关注字样 | Task 4 |
| recursion/timeout 上限 | Task 2（16 / 90s） |
| 单测断言推荐工具未挂载 | Task 1 |

Placeholder scan: 无 TBD。类型：`symbols_note`、`horizon: "3-5d"` 前后一致。
