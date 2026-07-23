# Agent 主要指数行情工具 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让投研助手通过按需工具 `fetch_market_indices` 获取与大盘页同源的主要指数点位（含科创50），禁止编造。

**Architecture:** 在 `market.py` 增加纯函数，把 `get_market()` 的 `featured` 裁成 agent 用的精简快照；`tools.py` 用 `@tool` 包装并注册；`SYSTEM_PROMPT` 强制问点位时调用该工具。

**Tech Stack:** Python、LangChain `@tool`、现有 `app.market.get_market()`、pytest（新建最小单测）

## Global Constraints

- 只返回 featured 主要指数，不返回 boards / summary / EXTRA
- 字段：`updated_at`、`source`、`indices[]`（`symbol/name/price/change/change_pct/amount`）
- 失败返回 `{"error": "...", "indices": []}`，不抛未捕获异常给 LLM
- 不改前端、不新增 HTTP API、不加个股报价
- 提交需用户明确要求后再做（本仓库习惯）；计划中的 commit 步骤默认跳过，除非用户要求

---

### File map

| 文件 | 职责 |
|------|------|
| `backend/app/market.py` | 新增 `featured_indices_snapshot()` 纯函数 |
| `backend/tests/test_market_indices_snapshot.py` | 单测（mock 输入，不打外网） |
| `backend/app/advisor/agent/tools.py` | `@tool fetch_market_indices` + 注册 |
| `backend/app/advisor/agent/graph.py` | SYSTEM_PROMPT 增加调用指引 |

---

### Task 1: 精简快照纯函数 + 单测

**Files:**
- Modify: `backend/app/market.py`（文件末尾 `get_market` 之后追加）
- Create: `backend/tests/test_market_indices_snapshot.py`
- Create: `backend/tests/__init__.py`（若目录不存在则空文件）

**Interfaces:**
- Produces: `featured_indices_snapshot(market: dict[str, Any]) -> dict[str, Any]`
- Consumes: `get_market()` 返回值形状中的 `updated_at` / `source` / `featured`

- [ ] **Step 1: 写失败单测**

创建 `backend/tests/__init__.py`（空）与：

```python
# backend/tests/test_market_indices_snapshot.py
from app.market import featured_indices_snapshot


def test_featured_indices_snapshot_keeps_star50_and_slim_fields():
    market = {
        "updated_at": "2026-07-21T12:00:00+08:00",
        "source": "eastmoney.ulist",
        "featured": [
            {
                "symbol": "000688",
                "name": "科创50",
                "price": 1001.23,
                "change": 10.5,
                "change_pct": 1.06,
                "open": 990.0,
                "high": 1010.0,
                "low": 980.0,
                "pre_close": 990.73,
                "volume": 1e9,
                "amount": 2e10,
                "featured": True,
            }
        ],
        "boards": {"gainers": [{"symbol": "600000"}]},
        "summary": {"amount_total": 1},
    }
    out = featured_indices_snapshot(market)
    assert out["updated_at"] == "2026-07-21T12:00:00+08:00"
    assert out["source"] == "eastmoney.ulist"
    assert "boards" not in out
    assert "summary" not in out
    assert len(out["indices"]) == 1
    row = out["indices"][0]
    assert row == {
        "symbol": "000688",
        "name": "科创50",
        "price": 1001.23,
        "change": 10.5,
        "change_pct": 1.06,
        "amount": 2e10,
    }


def test_featured_indices_snapshot_empty_featured():
    out = featured_indices_snapshot(
        {"updated_at": "t", "source": "s", "featured": []}
    )
    assert out["indices"] == []
```

- [ ] **Step 2: 跑测确认失败**

Run（在 `backend/` 下，已有 `.venv`）：

```bash
cd /Users/orange/Desktop/code/share-data/backend && .venv/bin/python -m pytest tests/test_market_indices_snapshot.py -v
```

Expected: FAIL，提示 `featured_indices_snapshot` 未定义或无法导入。

- [ ] **Step 3: 实现纯函数**

在 `backend/app/market.py` 末尾追加：

```python
def featured_indices_snapshot(market: dict[str, Any]) -> dict[str, Any]:
    """Slim featured-index payload for advisor agent tools."""
    indices: list[dict[str, Any]] = []
    for item in market.get("featured") or []:
        indices.append(
            {
                "symbol": item.get("symbol"),
                "name": item.get("name"),
                "price": item.get("price"),
                "change": item.get("change"),
                "change_pct": item.get("change_pct"),
                "amount": item.get("amount"),
            }
        )
    return {
        "updated_at": market.get("updated_at"),
        "source": market.get("source"),
        "indices": indices,
    }
```

- [ ] **Step 4: 跑测确认通过**

```bash
cd /Users/orange/Desktop/code/share-data/backend && .venv/bin/python -m pytest tests/test_market_indices_snapshot.py -v
```

Expected: 2 passed

- [ ] **Step 5: Commit（仅当用户要求）**

```bash
git add backend/app/market.py backend/tests/__init__.py backend/tests/test_market_indices_snapshot.py
git commit -m "$(cat <<'EOF'
feat(advisor): add featured index snapshot helper for agent

EOF
)"
```

---

### Task 2: 注册 `fetch_market_indices` 工具

**Files:**
- Modify: `backend/app/advisor/agent/tools.py`

**Interfaces:**
- Consumes: `app.market.get_market`, `app.market.featured_indices_snapshot`
- Produces: LangChain tool `fetch_market_indices`（无参数，返回 JSON str）；出现在 `build_tools` 返回列表中

- [ ] **Step 1: 写注册冒烟测（可放同文件或新测）**

追加到 `backend/tests/test_market_indices_snapshot.py`：

```python
import json
from unittest.mock import patch

from app.advisor.agent.tools import build_tools


def test_fetch_market_indices_tool_uses_snapshot():
    tools = build_tools("test-user")
    by_name = {t.name: t for t in tools}
    assert "fetch_market_indices" in by_name
    fake = {
        "updated_at": "t",
        "source": "eastmoney.ulist",
        "featured": [
            {
                "symbol": "000688",
                "name": "科创50",
                "price": 1.0,
                "change": 0.1,
                "change_pct": 0.2,
                "amount": 3.0,
                "featured": True,
            }
        ],
        "boards": {},
    }
    with patch("app.market.get_market", return_value=fake):
        raw = by_name["fetch_market_indices"].invoke({})
    data = json.loads(raw)
    assert data["indices"][0]["name"] == "科创50"
    assert "boards" not in data


def test_fetch_market_indices_tool_on_error():
    tools = {t.name: t for t in build_tools("u")}
    with patch("app.market.get_market", side_effect=RuntimeError("down")):
        raw = tools["fetch_market_indices"].invoke({})
    data = json.loads(raw)
    assert data["indices"] == []
    assert "error" in data
```

- [ ] **Step 2: 跑测确认失败（工具尚未注册）**

```bash
cd /Users/orange/Desktop/code/share-data/backend && .venv/bin/python -m pytest tests/test_market_indices_snapshot.py -v
```

Expected: Task1 的测仍过；新增两测 FAIL（缺 `fetch_market_indices`）

- [ ] **Step 3: 实现工具并注册**

在 `tools.py` 顶部 import 区附近增加（与其它顶层 import 一致）：

```python
from ...market import featured_indices_snapshot, get_market
```

在 `fetch_economic_calendar` 工具定义之后、`return [` 之前插入：

```python
    @tool
    def fetch_market_indices() -> str:
        """获取 A 股主要指数实时行情（上证/深成/创业板/科创50/沪深300 等）。
        用户问指数点位、涨跌、大盘概况时必须调用；勿编造点位。"""
        try:
            snap = featured_indices_snapshot(get_market())
        except Exception as exc:
            snap = {
                "error": f"{type(exc).__name__}: {exc}",
                "indices": [],
            }
        return json.dumps(snap, ensure_ascii=False, default=str)
```

在 `return [` 列表中、`fetch_economic_calendar` 之后追加：

```python
        fetch_market_indices,
```

注意：`patch("app.market.get_market")` 要求工具内通过 `app.market.get_market` 可被 patch。若相对导入导致 patch 路径需改为 `app.advisor.agent.tools.get_market`，以实现时实际 import 绑定为准：优先在工具内写：

```python
from app.market import featured_indices_snapshot, get_market
```

（与 `patch("app.market.get_market")` 对齐；若包内习惯相对导入，则测试改为 `patch("app.advisor.agent.tools.get_market")`。）

推荐实现：在 `build_tools` 内工具函数里局部 import，避免循环依赖，并与 patch 路径一致：

```python
    @tool
    def fetch_market_indices() -> str:
        """获取 A 股主要指数实时行情（上证/深成/创业板/科创50/沪深300 等）。
        用户问指数点位、涨跌、大盘概况时必须调用；勿编造点位。"""
        from app.market import featured_indices_snapshot, get_market

        try:
            snap = featured_indices_snapshot(get_market())
        except Exception as exc:
            snap = {
                "error": f"{type(exc).__name__}: {exc}",
                "indices": [],
            }
        return json.dumps(snap, ensure_ascii=False, default=str)
```

测试 patch 使用：`patch("app.market.get_market", ...)`

- [ ] **Step 4: 跑测确认通过**

```bash
cd /Users/orange/Desktop/code/share-data/backend && .venv/bin/python -m pytest tests/test_market_indices_snapshot.py -v
```

Expected: 4 passed

- [ ] **Step 5: Commit（仅当用户要求）**

```bash
git add backend/app/advisor/agent/tools.py backend/tests/test_market_indices_snapshot.py
git commit -m "$(cat <<'EOF'
feat(advisor): register fetch_market_indices agent tool

EOF
)"
```

---

### Task 3: SYSTEM_PROMPT 指引

**Files:**
- Modify: `backend/app/advisor/agent/graph.py`

**Interfaces:**
- Consumes: 工具名 `fetch_market_indices`（Task 2）
- Produces: 更新后的 `SYSTEM_PROMPT` 字符串

- [ ] **Step 1: 更新 prompt**

在 `SYSTEM_PROMPT` 规则列表中，于规则 5（宏观/政策）之后插入新规则（编号顺延或并入清晰一条）：

将：

```python
5. 宏观/政策：fetch_macro_china_snapshot、fetch_economic_calendar、fetch_market_cctv_news；无独立政治源，政治相关仅能间接参考联播等公开报道。
```

改为：

```python
5. 宏观/政策：fetch_macro_china_snapshot、fetch_economic_calendar、fetch_market_cctv_news；无独立政治源，政治相关仅能间接参考联播等公开报道。
6. 指数点位/涨跌/大盘概况：必须先调用 fetch_market_indices，不得编造点位；该工具覆盖上证、深成、创业板、科创50、沪深300 等主要指数。
```

并将原规则 6、7、8 改为 7、8、9（全文保持编号连续）。

完整期望片段：

```python
SYSTEM_PROMPT = """你是「投研助手」，次日顾问产品中的 AI 投研副驾（DeepSeek）。
对外自称「投研助手」；语气专业、简洁、务实，不卖弄术语，结论先行再补依据。
你可按需读取用户全部业务数据（真实持仓、模拟盘、策略、推荐归档、龙虎榜、LLM 配置状态），
并用自然语言协助配置真实持仓与操作模拟盘；也可拉取 AKShare 新闻/公告/研报/宏观/经济日历与主要指数行情。
规则：
1. 用中文 Markdown 回答；买卖建议仅供研究参考。
2. 需要事实时优先调用工具，不要编造名单、新闻、持仓、收益或指数点位。
3. 写操作（改持仓、模拟盘下单/清仓/重置、改策略）必须：先读现状 → 向用户复述拟执行内容 → 用户明确确认后再调用对应工具并传 confirm=true。未确认只展示预览。
4. 分析真实持仓用 analyze_portfolio_positions；可再拉新闻/公告补叙事。
5. 宏观/政策：fetch_macro_china_snapshot、fetch_economic_calendar、fetch_market_cctv_news；无独立政治源，政治相关仅能间接参考联播等公开报道。
6. 指数点位/涨跌/大盘概况：必须先调用 fetch_market_indices，不得编造点位；该工具覆盖上证、深成、创业板、科创50、沪深300 等主要指数。
7. 策略修改：propose 后展示 patch，用户确认再 apply_strategy_patch(confirm=true)。
8. 若无今日归档，引导去基础面板刷新候选池。
9. 回复末尾加一句免责声明。
"""
```

- [ ] **Step 2: 静态检查**

```bash
cd /Users/orange/Desktop/code/share-data/backend && .venv/bin/python -c "from app.advisor.agent.graph import SYSTEM_PROMPT; assert 'fetch_market_indices' in SYSTEM_PROMPT; print('ok')"
```

Expected: `ok`

- [ ] **Step 3: 再跑全部相关单测**

```bash
cd /Users/orange/Desktop/code/share-data/backend && .venv/bin/python -m pytest tests/test_market_indices_snapshot.py -v
```

Expected: 4 passed

- [ ] **Step 4: 手工验收（可选，需后端与 LLM 已配置）**

在投研助手对话中问：「科创50多少点？」  
期望：出现工具调用 `fetch_market_indices`，回答含点位与涨跌幅。

- [ ] **Step 5: Commit（仅当用户要求）**

```bash
git add backend/app/advisor/agent/graph.py
git commit -m "$(cat <<'EOF'
feat(advisor): require fetch_market_indices for index quotes

EOF
)"
```

---

## Spec coverage checklist

| Spec 要求 | Task |
|-----------|------|
| 工具 `fetch_market_indices` | Task 2 |
| 复用 `get_market()`，只返回 featured 精简字段 | Task 1 + 2 |
| SYSTEM_PROMPT 强制调用 | Task 3 |
| 错误返回 error + 空 indices | Task 2 测试 + 实现 |
| 不改前端 / 不加 boards / 不加个股报价 | 全局约束，无对应改动 |
| 验收「科创50多少点」 | Task 3 Step 4 |

## Self-review

- 无 TBD/TODO 占位
- 函数名与 patch 路径在 Task 2 已统一为 `app.market.get_market` + 局部 import
- 字段与 spec JSON 形状一致
