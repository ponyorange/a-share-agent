# 投委会定额资金次日组合 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让用户用一笔独立模拟资金发起投委会，从沪深主板股票和场内 ETF 中得到最多 3 只、满足整手与费用约束的次日模拟组合，并展示经过历史校准的上涨概率、预期涨幅、数量和剩余现金。

**Architecture:** 新增严格候选筛选、故障隔离日线采集、时间滚动概率校准和确定性整数分配四个纯领域模块。LangGraph 在 `prepare` 后生成权威预测，在 `trader` 后生成权威分配计划；回测、风控和主席只审核冻结计划，聊天与前端只引用 artifact，不从 LLM 文本解析数值。现有账户模式保持兼容，独立组合首版只输出建议，不写入现有模拟盘账户。

**Tech Stack:** Python 3.12、FastAPI、Pydantic v2、pandas、NumPy、LangGraph、MongoDB、Redis Streams、React 19、TypeScript 6、Vitest、Testing Library。

## Global Constraints

- `capital_amount` 是独立模拟资金，默认 UI 值为 5000 元；金额使用 `Decimal`/定点运算，不用二进制浮点累计资金。
- 候选范围仅为沪深主板股票和普通场内 ETF；排除创业板、科创板、北交所、ST、退市整理、停牌、数据不足和一手不可买标的。
- 现有 `hs` 语义不变；严格主板分类只在 `portfolio_mode=standalone` 且 `asset_scope=mainboard_and_etf` 时启用。
- 精确日线候选最多 30 只；单标的失败可跳过，失败比例超过 30% 或成功候选为空才中止。
- 上涨定义为下一交易日收盘相对本交易日收盘上涨。
- 权威上涨概率采用按资产类型分组的五分位分箱、Beta-Binomial 平滑和 PAVA 单调校准；不得使用 LLM `confidence` 代替。
- 可买门槛为 `up_probability >= 0.60`、扣除预计往返交易成本后的预期收益为正、校准样本不少于 40。
- 股票和普通 ETF 买入数量均为 100 的整数倍；最多 3 只；股票单标的上限 60%，ETF 单标的上限 70%，现金保留比例至少 5%。
- 独立模式风控使用 `committee.capital_allocation` 的仓位上限；不得套用账户模式 `risk_limits.max_single_position: 0.20`，也不得读取 paper 持仓。
- 无合格候选或无可行整手组合时生成正常的全现金计划：`action=hold`、`symbol=CASH`、`target_weight=0`。
- `forecast_candidates` 和 `allocation_plan` 是权威 artifact；聊天、主席和 UI 不得重新计算或改写概率、收益、数量和金额。
- 独立组合首版是建议模式：隐藏/禁用现有模拟盘审批入口，审批 API 返回 409；不修改 `ApprovalDialog` 的数量推算逻辑，也不写入用户现有持仓或现金。
- 不新增第三方依赖；复用 pandas、NumPy、Pydantic 和现有前端依赖。
- 旧会议、旧 API 请求和账户模式保持可读、可重试、可审批。
- 实施期间不得创建 Git commit，除非用户另行明确授权。

## File Structure

**Backend — new files**

- `backend/app/advisor/committee/capital_models.py`: 预测候选、预测集合、费用、分配仓位和分配计划的冻结 Pydantic 领域模型。
- `backend/app/advisor/committee/capital_universe.py`: 严格主板/ETF 分类、粗筛排序、资金可买性过滤和故障隔离日线预取。
- `backend/app/advisor/committee/next_day_forecast.py`: 历史样本构造、五分位校准、Beta-Binomial 平滑、PAVA 和次日预测。
- `backend/app/advisor/committee/capital_allocator.py`: 费用估算、整手候选组合枚举、确定性优先级排序和全现金计划。
- `backend/tests/test_committee_capital_models.py`: 新领域模型与哈希稳定性。
- `backend/tests/test_committee_capital_universe.py`: 严格分类、粗筛和单标的采集失败隔离。
- `backend/tests/test_committee_next_day_forecast.py`: 时间滚动、校准、数据泄漏和门槛。
- `backend/tests/test_committee_capital_allocator.py`: 5000 元、整手、费用、仓位、最多 3 只与全现金。

**Backend — modified files**

- `backend/app/advisor/config.yaml`: 增加 `committee.capital_allocation` 与费用配置。
- `backend/app/advisor/committee/routes.py`: 扩展创建请求、幂等哈希、重试参数和独立模式审批保护。
- `backend/app/advisor/committee/tasks.py`: 两阶段候选加载、预取日线注入、冻结独立现金、持久化两个新 artifact。
- `backend/app/advisor/committee/snapshot.py`: 支持注入已预取 K 线与独立资金账户项，不改变账户模式默认行为。
- `backend/app/advisor/committee/state.py`: 增加 `forecast_candidates`、`allocation_plan` 状态字段。
- `backend/app/advisor/committee/graph.py`: 增加 `forecast`、`allocate` 节点并锁定整份计划。
- `backend/app/advisor/committee/models.py`: `FinalDecision` 增加组合模式、计划哈希和仓位摘要，放宽“首条订单摘要”限制。
- `backend/app/advisor/committee/agents.py`: 主席输出 schema 同时支持旧账户语义和独立组合的 `accept_plan` 语义。
- `backend/app/advisor/committee/prompts.py`: 给角色注入权威预测/计划并明确禁止改写数值。
- `backend/app/advisor/committee/backtest.py`: 独立模式按精确数量评估；全现金计划返回确定性通过结果。
- `backend/app/advisor/committee/risk.py`: 对分配计划执行资金、集中度、流动性和数据质量硬风控。
- `backend/app/advisor/committee/approval.py`: 独立建议模式拒绝绑定/审批，不从权重重算数量。
- `backend/tests/test_committee_task5.py`: 创建请求、幂等与旧请求兼容。
- `backend/tests/test_committee_snapshot.py`: 注入 K 线和独立资金快照。
- `backend/tests/test_committee_graph.py`: 新拓扑、全现金、多标的计划锁定和主席篡改保护。
- `backend/tests/test_committee_backtest.py`: 精确数量与全现金回测。
- `backend/tests/test_committee_risk.py`: 计划级风险规则。
- `backend/tests/test_committee_execution_consistency.py`: 独立模式审批保护和计划哈希一致性。
- `backend/tests/test_committee_execution.py`: 主席两种输出 shape 的互斥校验。
- `backend/tests/test_committee_task5_review.py`: 新 artifact 重放与失败消息。

**Frontend**

- `frontend-advisor/src/committee/committeeApi.ts`: 创建请求与预测/分配 artifact 类型。
- `frontend-advisor/src/committee/components/CreateRunDialog.tsx`: 5000 元资金输入和固定“沪深主板 + ETF”独立模式。
- `frontend-advisor/src/committee/components/AllocationPlanCard.tsx`: 权威组合卡与全现金状态。
- `frontend-advisor/src/committee/components/CommitteeDetail.tsx`: 展示预测证据与组合卡，区分角色置信度和上涨概率。
- `frontend-advisor/src/committee/components/CommitteeChat.tsx`: 放行 `forecast_candidates`、`allocation_plan` 数据卡。
- `frontend-advisor/src/committee/CommitteePage.tsx`: 独立模式隐藏审批入口。
- `frontend-advisor/src/committee/committeeApi.test.ts`: 新请求序列化与类型化响应。
- `frontend-advisor/src/committee/CommitteePage.test.tsx`: 5000 元创建、组合展示、全现金、失败降级和审批隐藏。
- `frontend-advisor/src/styles.css`: 资金输入、组合表格、概率/风险标签和响应式布局。

---

### Task 1: 定义定额资金领域契约与创建请求

**Files:**
- Create: `backend/app/advisor/committee/capital_models.py`
- Create: `backend/tests/test_committee_capital_models.py`
- Modify: `backend/app/advisor/committee/routes.py:80-101,182-201,250-290,440-460`
- Modify: `backend/app/advisor/config.yaml:1-68`
- Modify: `backend/tests/test_committee_task5.py:20-130`

**Interfaces:**
- Produces: `AssetType`, `PortfolioMode`, `ForecastCandidate`, `ForecastCandidates`, `AllocationPosition`, `AllocationPlan`, `allocation_plan_hash(plan) -> str`
- Extends: `CommitteeRunCreateBody` with `portfolio_mode`, `capital_amount`, `asset_scope`, `max_positions`, `minimum_up_probability`
- Consumes later: Tasks 2–9 import these exact model names.

- [ ] **Step 1: 写领域模型和 API 校验失败测试**

```python
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.advisor.committee.capital_models import (
    AllocationPlan,
    AllocationPosition,
    ForecastCandidate,
    allocation_plan_hash,
)
from app.advisor.committee.routes import CommitteeRunCreateBody


def test_standalone_request_requires_positive_capital():
    with pytest.raises(ValidationError, match="capital_amount"):
        CommitteeRunCreateBody(
            symbols=(),
            boards=("hs", "etf"),
            horizon="next_day",
            strategy_version="default",
            portfolio_mode="standalone",
            asset_scope="mainboard_and_etf",
            capital_amount=Decimal("0"),
        )


def test_old_account_request_remains_valid():
    body = CommitteeRunCreateBody(
        symbols=("510300",),
        boards=(),
        horizon="next_day",
        strategy_version="default",
    )
    assert body.portfolio_mode == "account"
    assert body.capital_amount is None


def test_allocation_hash_is_stable_for_decimal_and_position_order():
    first = AllocationPlan(
        capital_amount=Decimal("5000.00"),
        invested_amount=Decimal("3900.00"),
        estimated_fees=Decimal("10.00"),
        cash_remaining=Decimal("1090.00"),
        positions=(
            AllocationPosition(
                symbol="510300",
                name="沪深300ETF",
                asset_type="etf",
                reference_price=Decimal("3.900"),
                quantity=1000,
                lot_size=100,
                estimated_amount=Decimal("3900.00"),
                portfolio_weight=Decimal("0.78"),
                up_probability=Decimal("0.63"),
                expected_return=Decimal("0.009"),
                return_interval=(Decimal("-0.01"), Decimal("0.02")),
                evidence_ids=("snap:kline",),
            ),
        ),
    )
    second = first.model_copy(
        update={"capital_amount": Decimal("5000.0")}
    )
    assert allocation_plan_hash(first) == allocation_plan_hash(second)
```

- [ ] **Step 2: 运行测试并确认失败**

Run:

```bash
cd backend && .venv/bin/pytest tests/test_committee_capital_models.py tests/test_committee_task5.py -q
```

Expected: FAIL because `capital_models` and new request fields do not exist.

- [ ] **Step 3: 实现冻结领域模型与稳定哈希**

在 `capital_models.py` 定义：

```python
from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

AssetType = Literal["mainboard_stock", "etf"]
PortfolioMode = Literal["account", "standalone"]


class CapitalModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ForecastCandidate(CapitalModel):
    symbol: str = Field(pattern=r"^\d{6}$")
    name: str = Field(min_length=1, max_length=128)
    asset_type: AssetType
    as_of: str = Field(min_length=1)
    reference_price: Decimal = Field(gt=0)
    raw_score: Decimal
    up_probability: Decimal = Field(ge=0, le=1)
    expected_return: Decimal
    return_interval: tuple[Decimal, Decimal]
    sample_count: int = Field(ge=0)
    historical_hit_rate: Decimal = Field(ge=0, le=1)
    calibration_version: str = Field(min_length=1, max_length=128)
    data_quality: Literal["eligible", "insufficient", "stale", "invalid"]
    evidence_ids: tuple[str, ...] = ()


class ForecastCandidates(CapitalModel):
    as_of: str
    calibration_version: str
    candidates: tuple[ForecastCandidate, ...]
    skipped_symbols: tuple[dict[str, str], ...] = ()


class AllocationPosition(CapitalModel):
    symbol: str = Field(pattern=r"^\d{6}$")
    name: str
    asset_type: AssetType
    reference_price: Decimal = Field(gt=0)
    quantity: int = Field(gt=0)
    lot_size: int = Field(gt=0)
    estimated_amount: Decimal = Field(gt=0)
    portfolio_weight: Decimal = Field(gt=0, le=1)
    up_probability: Decimal = Field(ge=0, le=1)
    expected_return: Decimal
    return_interval: tuple[Decimal, Decimal]
    evidence_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def whole_lots(self):
        if self.quantity % self.lot_size:
            raise ValueError("quantity must be an integer number of lots")
        return self


class AllocationPlan(CapitalModel):
    capital_amount: Decimal = Field(gt=0)
    invested_amount: Decimal = Field(ge=0)
    estimated_fees: Decimal = Field(ge=0)
    cash_remaining: Decimal = Field(ge=0)
    positions: tuple[AllocationPosition, ...] = ()
    reason_if_all_cash: str | None = None

    @model_validator(mode="after")
    def balances(self):
        total = self.invested_amount + self.estimated_fees + self.cash_remaining
        if total != self.capital_amount:
            raise ValueError("allocation plan does not balance")
        if not self.positions and not self.reason_if_all_cash:
            raise ValueError("all-cash plan requires a reason")
        return self


def allocation_plan_hash(plan: AllocationPlan) -> str:
    payload = plan.model_dump(mode="json", exclude_none=False)
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(encoded.encode()).hexdigest()
```

金额字段在构造前统一量化到 `Decimal("0.01")`；概率和收益统一量化到 `Decimal("0.000001")`。不要在 validator 内静默修正不平衡金额。

- [ ] **Step 4: 扩展创建请求并保持旧请求兼容**

`CommitteeRunCreateBody` 增加：

```python
portfolio_mode: Literal["account", "standalone"] = "account"
capital_amount: Decimal | None = Field(default=None, gt=0, max_digits=12, decimal_places=2)
asset_scope: Literal["requested", "mainboard_and_etf"] = "requested"
max_positions: int = Field(default=3, ge=1, le=3)
minimum_up_probability: Decimal = Field(
    default=Decimal("0.60"), ge=Decimal("0.50"), le=Decimal("0.90")
)
```

模型校验规则：

```python
if self.portfolio_mode == "standalone":
    if self.capital_amount is None:
        raise ValueError("standalone portfolio requires capital_amount")
    if self.asset_scope != "mainboard_and_etf":
        raise ValueError("standalone portfolio requires mainboard_and_etf")
if self.portfolio_mode == "account" and self.capital_amount is not None:
    raise ValueError("account portfolio must use frozen account capital")
```

将所有新字段纳入 `_request_hash` 和 `initial_input.snapshot_request`。重试端点从父 run 的 `initial_input.snapshot_request` 复制这些字段，不退回默认值。新增配置：

```yaml
committee:
  capital_allocation:
    max_precise_candidates: 30
    max_positions: 3
    minimum_up_probability: 0.60
    max_stock_weight: 0.60
    max_etf_weight: 0.70
    cash_reserve_ratio: 0.05
    minimum_calibration_samples: 40
    max_symbol_failure_ratio: 0.30
    stock_lot_size: 100
    etf_lot_size: 100
    beta_prior_alpha: 1.0
    beta_prior_beta: 1.0
    commission_rate: 0.0003
    minimum_commission: 5.0
    stock_sell_stamp_tax_rate: 0.0005
    price_buffer_ratio: 0.003
```

- [ ] **Step 5: 运行契约测试**

Run:

```bash
cd backend && .venv/bin/pytest tests/test_committee_capital_models.py tests/test_committee_task5.py -q
```

Expected: PASS，旧账户模式测试不变。

- [ ] **Step 6: 建立人工提交检查点**

Run:

```bash
git diff -- backend/app/advisor/committee/capital_models.py backend/app/advisor/committee/routes.py backend/app/advisor/config.yaml backend/tests/test_committee_capital_models.py backend/tests/test_committee_task5.py
```

Expected: 仅包含领域契约、配置和请求兼容改动；不创建 commit。

---

### Task 2: 严格主板候选筛选与故障隔离快照

**Files:**
- Create: `backend/app/advisor/committee/capital_universe.py`
- Create: `backend/tests/test_committee_capital_universe.py`
- Modify: `backend/app/advisor/committee/snapshot.py:358-428,652-760`
- Modify: `backend/app/advisor/committee/tasks.py:582-624`
- Modify: `backend/tests/test_committee_snapshot.py`

**Interfaces:**
- Produces: `classify_capital_asset(symbol) -> AssetType | None`
- Produces: `shortlist_candidates(rows, capital_amount, max_candidates, ...) -> tuple[CandidateQuote, ...]`
- Produces: `collect_klines_fault_isolated(symbols, fetcher, as_of, max_failure_ratio) -> KlineCollection`
- Consumes: `AssetType` from Task 1.

- [ ] **Step 1: 写严格分类、资金过滤和故障隔离失败测试**

```python
from decimal import Decimal

import pandas as pd
import pytest

from app.advisor.committee.capital_universe import (
    classify_capital_asset,
    collect_klines_fault_isolated,
    shortlist_candidates,
)


@pytest.mark.parametrize("symbol", ["000001", "001248", "002384", "003816", "600000", "601318", "603986", "605499"])
def test_mainboard_prefixes_are_included(symbol):
    assert classify_capital_asset(symbol) == "mainboard_stock"


@pytest.mark.parametrize("symbol", ["300308", "301583", "688001", "830000"])
def test_growth_star_and_bse_are_excluded(symbol):
    assert classify_capital_asset(symbol) is None


def test_shortlist_filters_one_lot_above_capital_and_limits_precise_count():
    rows = [
        {"symbol": f"600{i:03d}", "name": f"股票{i}", "price": 5 + i, "amount": 1e8, "pct_chg": 1, "volume_ratio": 1, "turnover": 2}
        for i in range(40)
    ]
    result = shortlist_candidates(
        rows,
        capital_amount=Decimal("5000"),
        max_candidates=30,
        stock_lot_size=100,
        etf_lot_size=100,
    )
    assert len(result) <= 30
    assert all(item.reference_price * item.lot_size <= Decimal("5000") for item in result)


def test_single_symbol_kline_failure_is_recorded_not_raised():
    frame = pd.DataFrame(
        {"time": ["2026-07-01"] * 40, "close": [10.0] * 40, "amount": [1e8] * 40, "volume": [1e6] * 40}
    )
    def fetch(symbol):
        if symbol == "001248":
            raise RuntimeError("RemoteDisconnected")
        return symbol, frame
    result = collect_klines_fault_isolated(
        ("600000", "001248", "510300"),
        fetcher=fetch,
        as_of="2026-07-23",
        max_failure_ratio=0.5,
    )
    assert tuple(result.frames) == ("510300", "600000")
    assert result.failures[0].symbol == "001248"
```

- [ ] **Step 2: 运行测试并确认失败**

Run:

```bash
cd backend && .venv/bin/pytest tests/test_committee_capital_universe.py tests/test_committee_snapshot.py -q
```

Expected: FAIL because strict shortlist and injectable snapshot path do not exist.

- [ ] **Step 3: 实现严格分类和确定性粗筛**

在 `capital_universe.py` 中：

```python
MAINBOARD_PREFIXES = ("000", "001", "002", "003", "600", "601", "603", "605")
ETF_PREFIXES = ("15", "16", "18", "51", "56", "58")


def classify_capital_asset(symbol: str) -> AssetType | None:
    normalized = "".join(ch for ch in str(symbol) if ch.isdigit())
    if len(normalized) != 6:
        return None
    if normalized.startswith(ETF_PREFIXES):
        return "etf"
    if normalized.startswith(MAINBOARD_PREFIXES):
        return "mainboard_stock"
    return None
```

`CandidateQuote` 保存 `symbol/name/asset_type/reference_price/lot_size/coarse_score`。过滤名称含 `ST`、`退`，价格非正、成交额不足现有风控阈值、至少一手金额超过资金的记录。粗筛分数复用 `advisor.screen.coarse_score(row)`；排序键固定为 `(-coarse_score, -amount, symbol)`，截取前 30，保证重放稳定。

- [ ] **Step 4: 实现日线预取的逐标的隔离**

`collect_klines_fault_isolated` 对排序后的 shortlist 逐只调用注入的 `fetcher`：

```python
for symbol in sorted(set(symbols)):
    try:
        name, frame = fetcher(symbol)
        work = frame[frame["time"].astype(str).str.slice(0, 10) <= as_of]
        if len(work) < 25:
            raise ValueError("insufficient_history")
        frames[symbol] = (name, work.copy())
    except Exception as exc:
        failures.append(
            SymbolCollectionFailure(
                symbol=symbol,
                error_type=type(exc).__name__,
                message=str(exc)[:200],
            )
        )
```

成功数为 0 或 `len(failures) / len(symbols) > max_failure_ratio` 时抛出带成功/失败计数的 `CriticalDataError`；否则返回成功 frame 与失败摘要。

- [ ] **Step 5: 将预取结果注入冻结快照**

给 `default_collector_specs` 增加两个可选参数：

```python
kline_source: Mapping[str, tuple[str, pd.DataFrame]] | None = None
standalone_capital: Decimal | None = None
```

有 `kline_source` 时，`kline` collector 只序列化预取 frame，不再次访问网络；有 `standalone_capital` 时，`portfolio_account` collector 返回：

```python
{
    "content": {
        "cash": float(standalone_capital),
        "equity": float(standalone_capital),
        "positions": [],
        "version": f"standalone:{standalone_capital}",
        "account_version": 0,
    },
    "data_as_of": as_of,
    "source": "committee.standalone",
}
```

`_snapshot_loader` 仅在独立模式执行：加载 board spot rows → 严格筛选最多 30 只 → 预取日线 → 用成功 symbol 构建 snapshot。把失败摘要写入新的非关键 snapshot item `collection_report`，字段名使用 `skipped_symbols`，不要使用会触发 SnapshotBuilder 降级检测的 `error/errors` 键。账户模式沿用原路径。

- [ ] **Step 6: 运行候选与快照测试**

Run:

```bash
cd backend && .venv/bin/pytest tests/test_committee_capital_universe.py tests/test_committee_snapshot.py tests/test_committee_critical.py -q
```

Expected: PASS；原有关键 collector 语义仍通过。

- [ ] **Step 7: 建立人工提交检查点**

Run:

```bash
git diff -- backend/app/advisor/committee/capital_universe.py backend/app/advisor/committee/snapshot.py backend/app/advisor/committee/tasks.py backend/tests/test_committee_capital_universe.py backend/tests/test_committee_snapshot.py
```

Expected: 独立模式最多拉 30 只日线，账户模式无行为变化。

---

### Task 3: 实现无未来数据的次日概率校准

**Files:**
- Create: `backend/app/advisor/committee/next_day_forecast.py`
- Create: `backend/tests/test_committee_next_day_forecast.py`
- Modify: `backend/app/advisor/committee/capital_models.py`

**Interfaces:**
- Produces: `pava_non_decreasing(values, weights) -> tuple[Decimal, ...]`
- Produces: `build_walk_forward_samples(frames, benchmark, minimum_history=25) -> tuple[CalibrationSample, ...]`
- Produces: `forecast_next_day(snapshot, config) -> ForecastCandidates`
- Consumes: `ForecastCandidate`, `ForecastCandidates` from Task 1; frozen `kline`/`market` items from Task 2.

- [ ] **Step 1: 写 PAVA、概率校准和未来数据隔离失败测试**

```python
from decimal import Decimal

import numpy as np
import pandas as pd

from app.advisor.committee.next_day_forecast import (
    calibrate_samples,
    pava_non_decreasing,
    walk_forward_scores,
)


def synthetic_ohlcv(size: int) -> pd.DataFrame:
    rng = np.random.default_rng(7)
    returns = rng.normal(0.001, 0.01, size=size)
    close = 10 * np.cumprod(1 + returns)
    volume = rng.integers(100_000, 500_000, size=size)
    return pd.DataFrame({
        "time": pd.date_range("2026-01-01", periods=size, freq="B").astype(str),
        "open": close * 0.999,
        "high": close * 1.01,
        "low": close * 0.99,
        "close": close,
        "volume": volume,
        "amount": close * volume,
    })


def test_pava_merges_decreasing_adjacent_blocks():
    result = pava_non_decreasing(
        (Decimal("0.40"), Decimal("0.70"), Decimal("0.50")),
        (10, 10, 10),
    )
    assert result == (Decimal("0.40"), Decimal("0.60"), Decimal("0.60"))


def test_beta_binomial_probability_is_smoothed():
    samples = [
        {"raw_score": Decimal("0.1"), "up": False, "return": Decimal("-0.01")},
        {"raw_score": Decimal("0.2"), "up": True, "return": Decimal("0.02")},
    ]
    bins = calibrate_samples(samples, bin_count=1, alpha=Decimal("1"), beta=Decimal("1"))
    assert bins[0].up_probability == Decimal("0.5")


def test_changing_future_close_does_not_change_earlier_scores():
    frame = synthetic_ohlcv(80)
    baseline = walk_forward_scores(frame, minimum_history=25)
    changed = frame.copy()
    changed.loc[changed.index[-1], "close"] *= 10
    replay = walk_forward_scores(changed, minimum_history=25)
    assert baseline[:-1] == replay[:-1]
```

该 fixture 使用固定 NumPy seed，测试不访问网络。

- [ ] **Step 2: 运行测试并确认失败**

Run:

```bash
cd backend && .venv/bin/pytest tests/test_committee_next_day_forecast.py -q
```

Expected: FAIL because forecasting module does not exist.

- [ ] **Step 3: 实现滚动样本和原始信号**

每个历史索引 `i` 只使用 `frame.iloc[:i + 1]` 计算特征，标签只读取 `frame.iloc[i + 1]`。使用现有：

```python
from ..features import compute_factors
from ..scoring import tech_score_from_factors
```

构造：

```python
factors = compute_factors(history, benchmark_history)
raw_score, _ = tech_score_from_factors(factors)
next_return = Decimal(str(next_close / current_close - 1))
```

当前预测只对最后一根已冻结 bar 计算 raw score，不读取下一根。股票和 ETF 的历史 `CalibrationSample` 分开汇总。

- [ ] **Step 4: 实现固定五分位校准**

对每个资产类型：

1. 按 `(raw_score, symbol, as_of)` 稳定排序；
2. 用索引等分为最多 5 个非空分位箱；
3. 概率为 `(up_count + alpha) / (sample_count + alpha + beta)`；
4. 对箱概率执行带 `sample_count` 权重的 PAVA；
5. 预期收益用 1%/99% 分位截尾后的均值；
6. 收益区间使用 10% 和 90% 分位；
7. 当前 raw score 映射到对应箱。

`calibration_version` 由算法名、配置和样本截止日稳定哈希生成。`sample_count < 40` 时 `data_quality="insufficient"`；其余合格记录为 `"eligible"`。

- [ ] **Step 5: 生成权威 ForecastCandidates**

`forecast_next_day` 从 snapshot 中读取：

- `kline`：每个成功 symbol 的 bars；
- `market`：市场状态；
- `collection_report`：跳过标的；
- snapshot `as_of` 和 evidence ID。

结果按 `(-up_probability, -expected_return, symbol)` 排序。每个 candidate 的 `evidence_ids` 至少包含 `"{snapshot_id}:kline"` 和 `"{snapshot_id}:market"`。

- [ ] **Step 6: 运行预测测试**

Run:

```bash
cd backend && .venv/bin/pytest tests/test_committee_next_day_forecast.py tests/test_committee_capital_models.py -q
```

Expected: PASS，包括未来数据扰动测试。

- [ ] **Step 7: 建立人工提交检查点**

Run:

```bash
git diff -- backend/app/advisor/committee/next_day_forecast.py backend/app/advisor/committee/capital_models.py backend/tests/test_committee_next_day_forecast.py
```

Expected: 权威概率完全由冻结历史数据确定，不包含 LLM 输入。

---

### Task 4: 实现 5000 元整手资金分配器

**Files:**
- Create: `backend/app/advisor/committee/capital_allocator.py`
- Create: `backend/tests/test_committee_capital_allocator.py`
- Modify: `backend/app/advisor/committee/capital_models.py`

**Interfaces:**
- Produces: `AllocationSettings`
- Produces: `estimate_round_trip_cost(candidate, quantity, settings) -> Decimal`
- Produces: `allocate_capital(forecasts, capital_amount, proposed_symbols, settings) -> AllocationPlan`
- Consumes: `ForecastCandidates`, `AllocationPlan` from Task 1/3.

- [ ] **Step 1: 写整手、费用、仓位和全现金失败测试**

```python
from decimal import Decimal

from app.advisor.committee.capital_allocator import (
    AllocationSettings,
    allocate_capital,
)
from app.advisor.committee.capital_models import (
    ForecastCandidate,
    ForecastCandidates,
)


def candidate(
    symbol: str,
    *,
    asset_type: str,
    price: str,
    probability: str,
    expected: str,
) -> ForecastCandidate:
    return ForecastCandidate(
        symbol=symbol,
        name=symbol,
        asset_type=asset_type,
        as_of="2026-07-23",
        reference_price=Decimal(price),
        raw_score=Decimal("0.8"),
        up_probability=Decimal(probability),
        expected_return=Decimal(expected),
        return_interval=(Decimal("-0.01"), Decimal("0.03")),
        sample_count=100,
        historical_hit_rate=Decimal(probability),
        calibration_version="test-v1",
        data_quality="eligible",
        evidence_ids=("snap:kline",),
    )


def stock(symbol: str, **kwargs) -> ForecastCandidate:
    return candidate(symbol, asset_type="mainboard_stock", **kwargs)


def etf(symbol: str, **kwargs) -> ForecastCandidate:
    return candidate(symbol, asset_type="etf", **kwargs)


def forecast_set(*items: ForecastCandidate) -> ForecastCandidates:
    return ForecastCandidates(
        as_of="2026-07-23",
        calibration_version="test-v1",
        candidates=items,
    )


def test_5000_plan_uses_whole_lots_and_balances():
    plan = allocate_capital(
        forecasts=forecast_set(
            stock("600000", price="10", probability="0.64", expected="0.012"),
            etf("510300", price="4", probability="0.62", expected="0.008"),
        ),
        capital_amount=Decimal("5000"),
        proposed_symbols=("600000", "510300"),
        settings=AllocationSettings(),
    )
    assert 1 <= len(plan.positions) <= 3
    assert all(item.quantity % 100 == 0 for item in plan.positions)
    assert plan.invested_amount + plan.estimated_fees + plan.cash_remaining == Decimal("5000.00")


def test_probability_below_sixty_percent_returns_all_cash():
    plan = allocate_capital(
        forecasts=forecast_set(
            etf("510300", price="4", probability="0.59", expected="0.02")
        ),
        capital_amount=Decimal("5000"),
        proposed_symbols=("510300",),
        settings=AllocationSettings(),
    )
    assert plan.positions == ()
    assert plan.cash_remaining == Decimal("5000.00")
    assert plan.reason_if_all_cash == "没有标的达到上涨概率与扣费后收益门槛"


def test_expensive_stock_over_weight_cap_is_skipped():
    plan = allocate_capital(
        forecasts=forecast_set(
            stock("600001", price="31", probability="0.70", expected="0.02"),
            etf("510300", price="4", probability="0.62", expected="0.01"),
        ),
        capital_amount=Decimal("5000"),
        proposed_symbols=("600001", "510300"),
        settings=AllocationSettings(),
    )
    assert [item.symbol for item in plan.positions] == ["510300"]
```

- [ ] **Step 2: 运行测试并确认失败**

Run:

```bash
cd backend && .venv/bin/pytest tests/test_committee_capital_allocator.py -q
```

Expected: FAIL because allocator does not exist.

- [ ] **Step 3: 实现可配置费用模型**

`AllocationSettings` 默认值与 YAML 完全一致。费用计算：

```python
buy_commission = max(notional * commission_rate, minimum_commission)
sell_commission = max(expected_sell_notional * commission_rate, minimum_commission)
stamp_tax = (
    expected_sell_notional * stock_sell_stamp_tax_rate
    if candidate.asset_type == "mainboard_stock"
    else Decimal("0")
)
```

买入占款使用 `reference_price * (1 + price_buffer_ratio)` 计算并向分取整；资格判断用 `expected_return * notional - buy_commission - sell_commission - stamp_tax > 0`。

- [ ] **Step 4: 实现确定性整数组合枚举**

先筛选 `eligible`、概率不少于 0.60、样本不少于 40、扣费后预期收益为正且在 `proposed_symbols` 中的候选。候选最多 30，枚举 1、2、3 个 symbol 的组合；每个组合按概率降序逐只分配不超过单标的上限的最大整手，再用剩余资金循环增加一手。

组合比较键固定为：

```python
(
    sum(position.portfolio_weight * position.up_probability for position in positions),
    sum(position.estimated_amount * position.expected_return for position in positions),
    invested_amount,
    -estimated_fees,
    tuple(position.symbol for position in positions),
)
```

前三项取最大；费用取最小；最后 symbol tuple 取字典序最小。最终金额量化到分，概率/收益量化到 6 位小数。无候选、无可行整手或扣费后收益不正时返回全现金。

- [ ] **Step 5: 运行分配器测试**

Run:

```bash
cd backend && .venv/bin/pytest tests/test_committee_capital_allocator.py tests/test_committee_capital_models.py -q
```

Expected: PASS，所有结果资金守恒。

- [ ] **Step 6: 建立人工提交检查点**

Run:

```bash
git diff -- backend/app/advisor/committee/capital_allocator.py backend/app/advisor/committee/capital_models.py backend/tests/test_committee_capital_allocator.py
```

Expected: 分配器不访问网络、不调用 LLM、不写数据库。

---

### Task 5: 将预测和分配接入 LangGraph 与持久化

**Files:**
- Modify: `backend/app/advisor/committee/state.py:215-237`
- Modify: `backend/app/advisor/committee/graph.py:443-552,554-911,913-1296`
- Modify: `backend/app/advisor/committee/prompts.py:18-40`
- Modify: `backend/app/advisor/committee/tasks.py:416-459`
- Modify: `backend/tests/test_committee_graph.py`
- Modify: `backend/tests/test_committee_task5_review.py`

**Interfaces:**
- Adds state: `forecast_candidates: dict[str, Any]`, `allocation_plan: dict[str, Any]`
- Adds dependency providers: `next_day_forecaster(snapshot, context)`, `capital_allocator(forecasts, proposals, context)`
- Adds graph nodes: `forecast`, `allocate`
- Consumes: Tasks 1–4.

- [ ] **Step 1: 写新拓扑和 artifact 失败测试**

```python
from decimal import Decimal

from app.advisor.committee.capital_models import (
    AllocationPlan,
    AllocationPosition,
    ForecastCandidate,
    ForecastCandidates,
)


async def fake_forecasts(snapshot, context):
    del context
    return ForecastCandidates(
        as_of=snapshot.as_of.isoformat(),
        calibration_version="test-v1",
        candidates=(
            ForecastCandidate(
                symbol="510300",
                name="沪深300ETF",
                asset_type="etf",
                as_of=snapshot.as_of.isoformat(),
                reference_price=Decimal("4.00"),
                raw_score=Decimal("0.80"),
                up_probability=Decimal("0.630000"),
                expected_return=Decimal("0.009000"),
                return_interval=(Decimal("-0.010000"), Decimal("0.020000")),
                sample_count=100,
                historical_hit_rate=Decimal("0.63"),
                calibration_version="test-v1",
                data_quality="eligible",
                evidence_ids=(f"{snapshot.snapshot_id}:kline",),
            ),
        ),
    )


async def fake_allocation(forecasts, proposals, context):
    del forecasts, proposals, context
    return AllocationPlan(
        capital_amount=Decimal("5000.00"),
        invested_amount=Decimal("4000.00"),
        estimated_fees=Decimal("5.00"),
        cash_remaining=Decimal("995.00"),
        positions=(
            AllocationPosition(
                symbol="510300",
                name="沪深300ETF",
                asset_type="etf",
                reference_price=Decimal("4.00"),
                quantity=1000,
                lot_size=100,
                estimated_amount=Decimal("4000.00"),
                portfolio_weight=Decimal("0.800000"),
                up_probability=Decimal("0.630000"),
                expected_return=Decimal("0.009000"),
                return_interval=(Decimal("-0.010000"), Decimal("0.020000")),
                evidence_ids=("a" * 64 + ":kline",),
            ),
        ),
    )


async def broken_snapshot(*_args, **_kwargs):
    raise RuntimeError("kline collection failed for 8/20 symbols")


def test_standalone_graph_persists_forecast_and_exact_allocation():
    result = invoke(
        build(
            FakeRunner(),
            next_day_forecaster=fake_forecasts,
            capital_allocator=fake_allocation,
        ),
        initial_state(
            snapshot_request={
                "portfolio_mode": "standalone",
                "capital_amount": "5000.00",
                "asset_scope": "mainboard_and_etf",
            }
        ),
    )
    assert result["forecast_candidates"]["candidates"][0]["up_probability"] == "0.630000"
    assert result["allocation_plan"]["positions"][0]["quantity"] % 100 == 0
    nodes = [event["node"] for event in result["events"]]
    assert nodes.index("forecast") < nodes.index("fundamental")
    assert nodes.index("trader") < nodes.index("allocate") < nodes.index("backtest")


def test_prepare_failure_emits_failed_data_message_not_fake_chair():
    result = invoke(build(FakeRunner(), snapshot_loader=broken_snapshot), initial_state())
    failed = [event for event in result["events"] if event["event_type"] == "message_completed"]
    assert failed[-1]["payload"]["role"] == "data"
    assert failed[-1]["payload"]["status"] == "failed"
    assert result["final_decision"]["symbol"] == "CASH"
```

- [ ] **Step 2: 运行图测试并确认失败**

Run:

```bash
cd backend && .venv/bin/pytest tests/test_committee_graph.py tests/test_committee_task5_review.py -q
```

Expected: FAIL because forecast/allocate state and nodes do not exist.

- [ ] **Step 3: 增加状态字段和依赖**

在 `CommitteeState` 添加：

```python
forecast_candidates: NotRequired[dict[str, Any]]
allocation_plan: NotRequired[dict[str, Any]]
```

Graph dependencies 提供默认确定性实现，测试可注入 fake。账户模式不执行新节点的业务逻辑：`forecast` 返回空更新，`allocate` 返回空更新，保持旧拓扑结果。

- [ ] **Step 4: 实现 forecast 节点**

独立模式调用 `forecast_next_day`，写入 state，并生成：

```python
{
    "forecast_candidates": forecasts.model_dump(mode="json"),
    "events": [
        _event(state, "forecast", "completed", eligible=eligible_count),
        _message_completed(
            state,
            node="forecast",
            role="quant",
            content=f"已完成 {len(candidates)} 只候选的历史校准，{eligible_count} 只达到可买门槛。",
            card_kind="forecast_candidates",
        ),
    ],
}
```

分析师、辩手和 trader prompt 都注入权威预测的只读 JSON，并明确：“`up_probability`/`expected_return` 是冻结数值，不得重算或改写；`confidence` 不是上涨概率。”

- [ ] **Step 5: 实现 allocate 节点与拓扑**

`allocate` 校验 trader symbols 必须属于 `forecast_candidates` 或为 `CASH`，再调用 `allocate_capital`。写入 `allocation_plan` 和 `allocation_plan` 完成消息。

拓扑变为：

```text
START -> prepare -> forecast -> fan_out
bull/bear -> trader -> allocate -> backtest -> risk -> chair -> END
```

任一节点 aborted 时保持现有终止规则。全现金计划仍继续经过确定性 backtest/risk/chair，不调用无意义的证券行情。

- [ ] **Step 6: 持久化新 artifact 并修复数据失败身份**

把 `forecast_candidates`、`allocation_plan` 加入 `reconcile_checkpoint_to_mongo.artifact_fields`。`prepare` 捕获 snapshot 异常时追加：

```python
_message_completed(
    state,
    node="prepare",
    role="data",
    content=f"市场数据冻结失败：{public_error_message(exc)}",
    status="failed",
)
```

`public_error_message` 只显示标的、失败数量和上游错误类别，不暴露堆栈或密钥。保守决策统一使用 `symbol="CASH"`。

- [ ] **Step 7: 运行图与重放测试**

Run:

```bash
cd backend && .venv/bin/pytest tests/test_committee_graph.py tests/test_committee_task5_review.py tests/test_committee_invoke.py -q
```

Expected: PASS；checkpoint reconcile 后两个新 artifact 可恢复。

- [ ] **Step 8: 建立人工提交检查点**

Run:

```bash
git diff -- backend/app/advisor/committee/state.py backend/app/advisor/committee/graph.py backend/app/advisor/committee/prompts.py backend/app/advisor/committee/tasks.py backend/tests/test_committee_graph.py backend/tests/test_committee_task5_review.py
```

Expected: 数值只来自权威 artifact，聊天仅引用。

---

### Task 6: 锁定整份组合的回测、风控与主席决策

**Files:**
- Modify: `backend/app/advisor/committee/models.py:331-442`
- Modify: `backend/app/advisor/committee/agents.py:91-110`
- Modify: `backend/app/advisor/committee/backtest.py`
- Modify: `backend/app/advisor/committee/risk.py`
- Modify: `backend/app/advisor/committee/graph.py:913-1207`
- Modify: `backend/app/advisor/committee/approval.py`
- Modify: `backend/app/advisor/committee/routes.py:700-880`
- Modify: `backend/tests/test_committee_backtest.py`
- Modify: `backend/tests/test_committee_risk.py`
- Modify: `backend/tests/test_committee_execution_consistency.py`
- Modify: `backend/tests/test_committee_execution.py`
- Modify: `backend/tests/test_committee_graph.py:580-648`

**Interfaces:**
- Extends: `FinalDecision.portfolio_mode`, `allocation_plan_hash`, `positions`
- Produces: `standalone_review_hash(proposals, allocation_plan) -> str`
- Produces: `ensure_approvable_portfolio_mode(mode) -> None`
- Consumes: `AllocationPlan`, exact position quantities.

- [ ] **Step 1: 写整份计划锁定与全现金失败测试**

```python
from decimal import Decimal

import pytest

from app.advisor.committee.approval import (
    ApprovalRejected,
    ensure_approvable_portfolio_mode,
)
from app.advisor.committee.capital_models import (
    AllocationPlan,
    AllocationPosition,
    allocation_plan_hash,
)
from app.advisor.committee.models import (
    FinalDecision,
    TradeDirection,
    VerdictStatus,
)


def test_final_decision_locks_allocation_plan_not_first_proposal():
    position = AllocationPosition(
        symbol="510300",
        name="沪深300ETF",
        asset_type="etf",
        reference_price=Decimal("4.00"),
        quantity=1000,
        lot_size=100,
        estimated_amount=Decimal("4000.00"),
        portfolio_weight=Decimal("0.800000"),
        up_probability=Decimal("0.630000"),
        expected_return=Decimal("0.009000"),
        return_interval=(Decimal("-0.010000"), Decimal("0.020000")),
    )
    plan = AllocationPlan(
        capital_amount=Decimal("5000.00"),
        invested_amount=Decimal("4000.00"),
        estimated_fees=Decimal("5.00"),
        cash_remaining=Decimal("995.00"),
        positions=(position,),
    )
    decision = FinalDecision(
        user_id="u",
        run_id="r",
        portfolio_mode="standalone",
        action=TradeDirection.BUY,
        symbol="510300",
        target_weight=Decimal("0.800000"),
        confidence=1,
        rationale="确认整份计划",
        risk_status=VerdictStatus.APPROVED,
        allocation_plan_hash=allocation_plan_hash(plan),
        positions=plan.positions,
    )
    assert decision.positions == plan.positions
    assert decision.orders == ()


def test_all_cash_decision_uses_cash_summary():
    plan = AllocationPlan(
        capital_amount=Decimal("5000.00"),
        invested_amount=Decimal("0.00"),
        estimated_fees=Decimal("0.00"),
        cash_remaining=Decimal("5000.00"),
        positions=(),
        reason_if_all_cash="没有合格标的",
    )
    decision = FinalDecision(
        user_id="u",
        run_id="r",
        portfolio_mode="standalone",
        action=TradeDirection.HOLD,
        symbol="CASH",
        target_weight=0,
        confidence=1,
        rationale="保持现金",
        risk_status=VerdictStatus.APPROVED,
        allocation_plan_hash=allocation_plan_hash(plan),
        positions=(),
    )
    assert decision.symbol == "CASH"
    assert decision.target_weight == 0


def test_standalone_order_preview_is_advisory_only():
    with pytest.raises(
        ApprovalRejected,
        match="独立资金组合仅提供模拟建议",
    ):
        ensure_approvable_portfolio_mode("standalone")
```

- [ ] **Step 2: 运行决策测试并确认失败**

Run:

```bash
cd backend && .venv/bin/pytest tests/test_committee_backtest.py tests/test_committee_risk.py tests/test_committee_execution_consistency.py tests/test_committee_graph.py -q
```

Expected: FAIL because current chair and approval still use the first proposal/target weight.

- [ ] **Step 3: 扩展 FinalDecision 并兼容旧会议**

新增默认字段：

```python
portfolio_mode: PortfolioMode = "account"
allocation_plan_hash: str | None = Field(default=None, min_length=64, max_length=64)
positions: tuple[AllocationPosition, ...] = ()
```

validator 规则：

- `portfolio_mode="account"` 时保留现有 `orders == proposals` 和首条订单摘要约束；
- `portfolio_mode="standalone"` 时要求 `allocation_plan_hash`；
- standalone 有仓位时顶层摘要等于 `positions[0]` 的 symbol，action 为 buy，target_weight 等于首仓位权重；
- standalone 无仓位时必须为 `hold/CASH/0`；
- standalone 不写 legacy `orders`，避免被现有审批误执行。

- [ ] **Step 4: 扩展主席输出 schema**

`ChairOutput` 同时容纳两种互斥 shape：

```python
class ChairOutput(BaseModel):
    model_config = {"extra": "forbid"}
    chat_message: str = Field(min_length=1, max_length=12000)
    action: Literal["buy", "sell", "hold"] | None = None
    symbol: str | None = Field(default=None, min_length=1, max_length=256)
    target_weight: float | None = Field(default=None, ge=0, le=1)
    accept_plan: bool | None = None
    confidence: float = Field(ge=0, le=1)
    rationale: str = Field(min_length=1, max_length=12000)
    evidence_ids: tuple[NonEmptyStr, ...] = ()

    @model_validator(mode="after")
    def exactly_one_shape(self):
        legacy = (self.action, self.symbol, self.target_weight)
        legacy_any = any(value is not None for value in legacy)
        legacy_complete = all(value is not None for value in legacy)
        if self.accept_plan is not None and legacy_any:
            raise ValueError("use legacy chair shape or accept_plan shape")
        if self.accept_plan is None and not legacy_complete:
            raise ValueError("legacy chair shape is incomplete")
        return self
```

在 `test_committee_execution.py` 覆盖 legacy、plan shape 均通过，以及混用/缺字段拒绝。

- [ ] **Step 5: 让回测和风控审核精确计划**

独立模式 backtest 输入为 `AllocationPlan`：

- 使用冻结 `quantity`、`reference_price` 和费用；
- 组合收益按初始资金加权；
- 全现金直接返回 `passed=True`、`score=1`、`summary="无合格交易，保持现金"`；
- verdict hash 使用 `sha256(proposal_semantics_hash + allocation_plan_hash)`。

独立模式 risk 增加硬规则，并使用独立仓位上限：

```text
capital_balance
whole_lot
max_positions
max_stock_weight          # 0.60，覆盖账户模式 0.20
max_etf_weight            # 0.70，覆盖账户模式 0.20
minimum_up_probability
positive_net_expected_return
data_quality
liquidity
```

`portfolio_provider` 在 standalone 下必须返回空持仓；不得调用 `_portfolio_worker`。任一 hard 失败时状态为 rejected，主席只能生成全现金计划。

- [ ] **Step 6: 改造主席为确认整份计划**

主席 prompt 接收完整 `allocation_plan` 和 hash。LLM 只输出 `accept_plan: bool`、`confidence`、`rationale`、`chat_message`、`evidence_ids`；不再让 standalone 主席输出 symbol/weight。最终值由图节点确定：

```python
if risk_rejected or not output.accept_plan:
    locked_plan = all_cash_plan(capital_amount, reason)
else:
    locked_plan = reviewed_plan
```

创建 `FinalDecision` 时复制 locked plan 的全部 positions 和 hash。若模型输出中夹带与计划冲突的数值，忽略该文本字段并记录 validation error；不得要求模型重算。

- [ ] **Step 7: 保护审批入口**

`order-preview`、`bind preview`、`approve` 对 `portfolio_mode=standalone` 统一返回 HTTP 409 和固定中文信息。账户模式继续由 `target_weight * equity / quote` 生成模拟盘订单。

- [ ] **Step 8: 运行决策一致性测试**

Run:

```bash
cd backend && .venv/bin/pytest tests/test_committee_backtest.py tests/test_committee_risk.py tests/test_committee_execution.py tests/test_committee_execution_consistency.py tests/test_committee_graph.py tests/test_committee_approve_recovery.py -q
```

Expected: PASS；账户模式审批回归不变。

- [ ] **Step 9: 建立人工提交检查点**

Run:

```bash
git diff -- backend/app/advisor/committee/models.py backend/app/advisor/committee/agents.py backend/app/advisor/committee/backtest.py backend/app/advisor/committee/risk.py backend/app/advisor/committee/graph.py backend/app/advisor/committee/approval.py backend/app/advisor/committee/routes.py
```

Expected: standalone 只建议，账户模式仍可审批。

---

### Task 7: 前端创建请求与资金输入

**Files:**
- Modify: `frontend-advisor/src/committee/committeeApi.ts:20-98`
- Modify: `frontend-advisor/src/committee/components/CreateRunDialog.tsx`
- Modify: `frontend-advisor/src/committee/committeeApi.test.ts`
- Modify: `frontend-advisor/src/committee/CommitteePage.test.tsx`
- Modify: `frontend-advisor/src/styles.css`

**Interfaces:**
- Extends: `CommitteeRunCreate`
- Produces: `ForecastCandidate`, `ForecastCandidates`, `AllocationPosition`, `AllocationPlan` TypeScript types
- Consumes later: Task 8 imports these types.

- [ ] **Step 1: 写 5000 元创建请求失败测试**

```tsx
it('creates a standalone mainboard and ETF meeting with 5000 yuan', async () => {
  api.createCommitteeRun.mockResolvedValue({ run_id: 'run-new', status: 'created' })
  const user = userEvent.setup()
  render(<MemoryRouter><CommitteePage /></MemoryRouter>)
  await user.click(await screen.findByRole('button', { name: '发起投委会' }))
  const capital = screen.getByRole('spinbutton', { name: '模拟资金' })
  expect(capital).toHaveValue(5000)
  await user.click(screen.getByRole('button', { name: '确认发起' }))
  expect(api.createCommitteeRun).toHaveBeenCalledWith({
    symbols: [],
    boards: ['hs', 'etf'],
    horizon: 'next_day',
    strategy_version: 'default',
    portfolio_mode: 'standalone',
    capital_amount: 5000,
    asset_scope: 'mainboard_and_etf',
    max_positions: 3,
    minimum_up_probability: 0.6,
  }, expect.stringMatching(/^committee-create:/))
})
```

再覆盖 0、负数、超过两位小数和空值的本地校验。

- [ ] **Step 2: 运行前端测试并确认失败**

Run:

```bash
cd frontend-advisor && npm test -- src/committee/committeeApi.test.ts src/committee/CommitteePage.test.tsx
```

Expected: FAIL because create type and dialog do not send capital fields.

- [ ] **Step 3: 扩展 TypeScript 契约**

```ts
export type CommitteeRunCreate = {
  symbols: string[]
  boards: Array<'etf' | 'hs' | 'star'>
  horizon: 'next_day'
  strategy_version: string
  portfolio_mode: 'account' | 'standalone'
  capital_amount?: number
  asset_scope: 'requested' | 'mainboard_and_etf'
  max_positions: number
  minimum_up_probability: number
}

export type AllocationPlan = {
  capital_amount: string
  invested_amount: string
  estimated_fees: string
  cash_remaining: string
  positions: AllocationPosition[]
  reason_if_all_cash?: string | null
}
```

Decimal JSON 字段按字符串接收，组件中只在格式化时转为 `Number`，不得参与重新分配。

- [ ] **Step 4: 改造创建弹窗**

增加 `type="number"` 的“模拟资金”输入，默认 `5000`，`min=1`、`step=0.01`。独立模式固定展示：

- 候选范围：沪深主板 + ETF；
- 预测周期：下一交易日；
- 最多 3 只；
- 低于 60% 可全部持有现金；
- “模拟建议，不保证收益”。

提交 body 固定 `boards: ['hs', 'etf']`，不显示科创板 checkbox；仍允许输入明确的沪深主板/ETF 代码。错误文案明确到金额原因。

- [ ] **Step 5: 运行创建流程测试**

Run:

```bash
cd frontend-advisor && npm test -- src/committee/committeeApi.test.ts src/committee/CommitteePage.test.tsx
```

Expected: PASS。

- [ ] **Step 6: 建立人工提交检查点**

Run:

```bash
git diff -- frontend-advisor/src/committee/committeeApi.ts frontend-advisor/src/committee/components/CreateRunDialog.tsx frontend-advisor/src/committee/committeeApi.test.ts frontend-advisor/src/committee/CommitteePage.test.tsx frontend-advisor/src/styles.css
```

Expected: 只改变新会议创建 UI，历史会议加载不受影响。

---

### Task 8: 前端权威组合卡、概率证据与聊天卡片

**Files:**
- Create: `frontend-advisor/src/committee/components/AllocationPlanCard.tsx`
- Modify: `frontend-advisor/src/committee/components/CommitteeDetail.tsx:108-145,235-318`
- Modify: `frontend-advisor/src/committee/components/CommitteeChat.tsx:35-110`
- Modify: `frontend-advisor/src/committee/CommitteePage.tsx`
- Modify: `frontend-advisor/src/committee/CommitteePage.test.tsx`
- Modify: `frontend-advisor/src/styles.css`

**Interfaces:**
- Produces: `AllocationPlanCard({ plan, forecast, asOf })`
- Consumes: Task 7 TypeScript types and `forecast_candidates`/`allocation_plan` artifacts.

- [ ] **Step 1: 写组合卡、全现金与审批隐藏失败测试**

```tsx
it('renders exact quantities and calibrated probabilities from allocation artifact', async () => {
  const standaloneRun = {
    ...completedRun,
    initial_input: {
      snapshot_request: {
        portfolio_mode: 'standalone',
        capital_amount: '5000.00',
        asset_scope: 'mainboard_and_etf',
      },
    },
  }
  api.listCommitteeRuns.mockResolvedValue({ runs: [standaloneRun] })
  api.getCommitteeRun.mockResolvedValue({
    run: standaloneRun,
    events: [],
    artifacts: [
      {
        artifact_id: 'forecast',
        kind: 'forecast_candidates',
        payload: {
          as_of: '2026-07-23',
          calibration_version: 'v1',
          candidates: [],
          skipped_symbols: [],
        },
      },
      {
        artifact_id: 'allocation',
        kind: 'allocation_plan',
        payload: {
        capital_amount: '5000.00',
        invested_amount: '4000.00',
        estimated_fees: '5.00',
        cash_remaining: '995.00',
        positions: [{
          symbol: '510300',
          name: '沪深300ETF',
          asset_type: 'etf',
          reference_price: '4.000',
          quantity: 1000,
          lot_size: 100,
          estimated_amount: '4000.00',
          portfolio_weight: '0.800000',
          up_probability: '0.630000',
          expected_return: '0.009000',
          return_interval: ['-0.010000', '0.020000'],
          evidence_ids: ['snap:kline'],
        }],
        },
      },
    ],
  })
  render(<MemoryRouter><CommitteePage /></MemoryRouter>)
  expect(await screen.findByText('沪深300ETF')).toBeInTheDocument()
  expect(screen.getByText('1000 份')).toBeInTheDocument()
  expect(screen.getByText('63.0%')).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: '审批执行' })).not.toBeInTheDocument()
})


it('renders all cash as a normal result', async () => {
  api.getCommitteeRun.mockResolvedValue({
    run: completedRun,
    events: [],
    artifacts: [{
      artifact_id: 'cash',
      kind: 'allocation_plan',
      payload: {
        capital_amount: '5000.00',
        invested_amount: '0.00',
        estimated_fees: '0.00',
        cash_remaining: '5000.00',
        positions: [],
        reason_if_all_cash: '没有标的达到上涨概率与扣费后收益门槛',
      },
    }],
  })
  render(<MemoryRouter><CommitteePage /></MemoryRouter>)
  expect(await screen.findByText('建议保持现金')).toBeInTheDocument()
  expect(screen.getByText('¥5,000.00')).toBeInTheDocument()
  expect(screen.queryByRole('alert')).not.toHaveTextContent('建议保持现金')
})
```

- [ ] **Step 2: 运行页面测试并确认失败**

Run:

```bash
cd frontend-advisor && npm test -- src/committee/CommitteePage.test.tsx
```

Expected: FAIL because allocation card and card-kind whitelist do not exist.

- [ ] **Step 3: 实现 AllocationPlanCard**

组件只读取 artifact，格式化规则：

```ts
const money = new Intl.NumberFormat('zh-CN', {
  style: 'currency',
  currency: 'CNY',
  minimumFractionDigits: 2,
})
const percent = (value: string) => `${(Number(value) * 100).toFixed(1)}%`
```

每行展示名称/代码、股票或 ETF、参考价、`quantity` + “股/份”、金额、占比、上涨概率、预期涨幅、P10–P90 区间。上涨概率标题写“历史校准上涨概率”，角色报告仍写“观点置信度”。

全现金时显示正常状态卡、原因和剩余现金；不使用 error/failed class。

- [ ] **Step 4: 接入详情、群聊和审批保护**

`CommitteeDetail` 读取最后一个 `forecast_candidates` 和 `allocation_plan` artifact，最终组合区域优先渲染 `AllocationPlanCard`；旧会议没有新 artifact 时保留原 `final_decision` 表格。

`CommitteeChat` 数据卡白名单增加：

```ts
forecast_candidates: [
  ['合格候选', 'candidates.length'],
  ['校准版本', 'calibration_version'],
],
allocation_plan: [
  ['投入金额', 'invested_amount'],
  ['预计费用', 'estimated_fees'],
  ['剩余现金', 'cash_remaining'],
],
```

`CommitteePage` 根据 `selectedRun.initial_input.snapshot_request.portfolio_mode` 判断独立模式，隐藏 ApprovalDialog 入口。首版不改 `ApprovalDialog` 内部的 `weight × equity / price` 推算；独立模式根本不打开该对话框。

- [ ] **Step 5: 增加数据助手失败展示**

prepare 的 `message_completed(status=failed, role=data)` 作为普通失败气泡显示完整公开原因；不得从 `final_decision` fallback 生成主席气泡。历史旧 run 的 `UNKNOWN` fallback 仅保留兼容，不用于新 standalone run。

- [ ] **Step 6: 运行页面和 API 测试**

Run:

```bash
cd frontend-advisor && npm test -- src/committee/CommitteePage.test.tsx src/committee/chatMessages.test.ts src/committee/committeeApi.test.ts
```

Expected: PASS。

- [ ] **Step 7: 运行 lint 与构建**

Run:

```bash
cd frontend-advisor && npm run lint && npm run build
```

Expected: 两条命令退出码 0。

- [ ] **Step 8: 建立人工提交检查点**

Run:

```bash
git diff -- frontend-advisor/src/committee/components/AllocationPlanCard.tsx frontend-advisor/src/committee/components/CommitteeDetail.tsx frontend-advisor/src/committee/components/CommitteeChat.tsx frontend-advisor/src/committee/CommitteePage.tsx frontend-advisor/src/committee/CommitteePage.test.tsx frontend-advisor/src/styles.css
```

Expected: 新旧会议都有明确渲染路径。

---

### Task 9: 端到端回归、配置验证与运行手册

**Files:**
- Modify: `backend/tests/test_committee_e2e.py`
- Modify: `backend/tests/test_committee_critical.py`
- Modify: `frontend-advisor/src/committee/CommitteePage.test.tsx`
- Modify: `docs/superpowers/specs/2026-07-24-committee-capital-allocation-design.md`

**Interfaces:**
- Verifies all Tasks 1–8.
- Produces no new runtime API.

- [ ] **Step 1: 增加后端端到端测试**

用注入的 spot、K 线、forecast、LLM、回测和风控 provider 创建独立 run，断言：

```python
assert run.status is RunStatus.COMPLETED
assert artifacts["forecast_candidates"]["candidates"]
assert len(artifacts["allocation_plan"]["positions"]) <= 3
assert all(p["quantity"] % 100 == 0 for p in artifacts["allocation_plan"]["positions"])
assert Decimal(artifacts["allocation_plan"]["invested_amount"]) + Decimal(
    artifacts["allocation_plan"]["estimated_fees"]
) + Decimal(artifacts["allocation_plan"]["cash_remaining"]) == Decimal("5000.00")
assert artifacts["final_decision"]["allocation_plan_hash"] == allocation_plan_hash(
    AllocationPlan.model_validate(artifacts["allocation_plan"])
)
```

另建一例让 2/20 只日线失败，会议仍完成且数据助手报告 2 只跳过；再建一例 8/20 失败，会议在 prepare 失败并产生 data failed message。

- [ ] **Step 2: 运行后端定向测试**

Run:

```bash
cd backend && .venv/bin/pytest \
  tests/test_committee_capital_models.py \
  tests/test_committee_capital_universe.py \
  tests/test_committee_next_day_forecast.py \
  tests/test_committee_capital_allocator.py \
  tests/test_committee_snapshot.py \
  tests/test_committee_graph.py \
  tests/test_committee_backtest.py \
  tests/test_committee_risk.py \
  tests/test_committee_execution_consistency.py \
  tests/test_committee_e2e.py -q
```

Expected: PASS。

- [ ] **Step 3: 运行后端完整投委会测试**

Run:

```bash
cd backend && .venv/bin/pytest tests/test_committee*.py -q
```

Expected: PASS，无账户模式回归。

- [ ] **Step 4: 运行前端完整测试**

Run:

```bash
cd frontend-advisor && npm test
```

Expected: PASS。

- [ ] **Step 5: 进行本地人工验收**

启动现有 API、worker 和前端后，创建 5000 元会议并核对：

1. 创建请求是 `standalone/mainboard_and_etf`；
2. 日线精确采集不超过 30 只；
3. 数据助手先报告冻结和跳过情况；
4. 权威概率与聊天引用一致；
5. 最终最多 3 只且数量为 100 整数倍；
6. 投入金额 + 费用 + 剩余现金 = 5000 元；
7. 上涨概率低于 60% 时显示全现金；
8. 页面不显示审批执行按钮；
9. 刷新后 artifact、聊天和组合卡一致；
10. 重试使用原资金、门槛和候选范围。

- [ ] **Step 6: 更新设计文档的实施基线**

在设计文档补充最终实现选择：

- standalone 首版仅建议，不写现有模拟盘；
- 精确候选上限 30；
- 五分位 + Beta-Binomial + PAVA；
- 独立模式审批端点返回 409；
- 旧账户模式不变。

不得写尚未验证的性能或命中率数字。

- [ ] **Step 7: 最终差异与工作区检查**

Run:

```bash
git status --short && git diff --stat
```

Expected: 仅包含本计划列出的源文件、测试和设计文档；无 `.env`、凭据、数据库导出或临时浏览器文件。

## Final Verification Checklist

- [ ] `capital_amount` 进入请求哈希、冻结输入和重试输入。
- [ ] 严格主板不包含 30/68/4/8 开头证券。
- [ ] 精确日线不超过 30 只，单只失败不会中止。
- [ ] 历史标签和校准无未来数据泄漏。
- [ ] 股票与 ETF 分开校准，样本不足不进入组合。
- [ ] 60% 门槛和扣费后正收益同时生效。
- [ ] 数量均为 100 的整数倍，资金精确守恒。
- [ ] 最多 3 只、股票 60%、ETF 70%、现金保留 5%。
- [ ] 无机会时是正常全现金计划。
- [ ] 主席确认整份 plan，不能修改权威数值。
- [ ] 新 artifact 可持久化、刷新、重连和重试。
- [ ] standalone 不触发现有模拟盘审批。
- [ ] 旧会议和账户模式全套测试通过。
- [ ] 前端明确区分“历史校准上涨概率”和“观点置信度”。
- [ ] 页面固定展示模拟建议与不保证收益提示。
