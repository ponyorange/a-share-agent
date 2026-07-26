# AKShare 基金详情 Tab Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 AKShare 数据面板新增「基金详情」Tab，支持场外开放式基金的搜索、档案与单位净值展示，并支持 `?symbol=` 深链。

**Architecture:** 后端 `app/fund.py` 聚合 AKShare（搜索名单缓存 + overview + 净值序列），经 `AkshareProvider` 与 `GET /api/{source}/fund/*` 暴露；前端新增 `FundPage` / `fundApi`，导航 feature 为 `fund`，默认代码 `025857`。

**Tech Stack:** FastAPI、AKShare、pytest（mock）、React + React Router、Vitest、轻量 SVG 折线（不新增图表依赖）

## Global Constraints

- 仅场外开放式基金；不接 ETF / 货币基金专用分支
- 仅 AKShare 具备 `fund` feature；其它源 404 / 前端 Navigate 回 explorer
- 搜索：`fund_name_em` 进程内缓存约 1 小时；匹配代码前缀、简称包含、拼音缩写前缀（大小写不敏感）
- 详情：`fund_overview_em` + `fund_open_fund_info_em(indicator="单位净值走势")`
- 默认 `symbol=025857`；非法代码非 6 位数字 → 400
- 档案成功、净值失败时：`nav=null` + `nav_error` 字符串，不拖垮整页
- 不引入重型图表库；净值图用 SVG polyline
- 提交需用户明确要求后再做；计划中的 commit 步骤默认跳过，除非用户要求

---

### File map

| 文件 | 职责 |
|------|------|
| `backend/app/fund.py` | 搜索缓存、详情聚合、字段映射 |
| `backend/tests/test_fund.py` | mock AKShare 的搜索/详情单测 |
| `backend/app/providers/akshare_provider.py` | `features` + `get_fund_search` / `get_fund_detail` |
| `backend/app/main.py` | HTTP 路由 |
| `frontend/src/sources.ts` | `fund` feature 与 path |
| `frontend/src/components/PageNav.tsx` | 「基金详情」Tab |
| `frontend/src/main.tsx` | 路由与 `/fund` 重定向 |
| `frontend/src/fundApi.ts` | API 客户端与类型 |
| `frontend/src/fundApi.test.ts` | 解析/格式化单测 |
| `frontend/src/FundPage.tsx` | 页面（搜索、档案、净值图/表） |
| `frontend/src/styles.css` | `fund-*` 样式 |
| `README.md` | 入口链接 |

---

### Task 1: 后端搜索核心 + 单测

**Files:**
- Create: `backend/app/fund.py`
- Create: `backend/tests/test_fund.py`

**Interfaces:**
- Produces:
  - `search_funds(q: str, limit: int = 20) -> list[dict[str, Any]]`  
    每项：`{"symbol","name","type","pinyin"}`
  - `clear_fund_name_cache() -> None`（测试用）
  - 模块常量 `FUND_NAME_CACHE_TTL_SEC = 3600`
- Consumes: `ak.fund_name_em()` DataFrame 列：`基金代码` / `基金简称` / `基金类型` / `拼音缩写`

- [ ] **Step 1: 写失败单测**

```python
# backend/tests/test_fund.py
from __future__ import annotations

import pandas as pd
import pytest

from app import fund as fund_mod


@pytest.fixture(autouse=True)
def _clear_cache():
    fund_mod.clear_fund_name_cache()
    yield
    fund_mod.clear_fund_name_cache()


def _names_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "基金代码": "025857",
                "拼音缩写": "HXZZDWSBZTETFFQSLJC",
                "基金简称": "华夏中证电网设备主题ETF发起式联接C",
                "基金类型": "指数型-股票",
                "拼音全称": "HUAXIA...",
            },
            {
                "基金代码": "000001",
                "拼音缩写": "HXCZHH",
                "基金简称": "华夏成长混合",
                "基金类型": "混合型-灵活",
                "拼音全称": "HUAXIACHENGZHANGHUNHE",
            },
        ]
    )


def test_search_by_code_prefix(monkeypatch):
    monkeypatch.setattr(fund_mod.ak, "fund_name_em", _names_df)
    items = fund_mod.search_funds("025", limit=20)
    assert len(items) == 1
    assert items[0]["symbol"] == "025857"
    assert items[0]["name"].startswith("华夏中证电网")
    assert items[0]["pinyin"] == "HXZZDWSBZTETFFQSLJC"


def test_search_by_name_substring(monkeypatch):
    monkeypatch.setattr(fund_mod.ak, "fund_name_em", _names_df)
    items = fund_mod.search_funds("电网", limit=20)
    assert [i["symbol"] for i in items] == ["025857"]


def test_search_by_pinyin_prefix_case_insensitive(monkeypatch):
    monkeypatch.setattr(fund_mod.ak, "fund_name_em", _names_df)
    items = fund_mod.search_funds("hxzz", limit=20)
    assert [i["symbol"] for i in items] == ["025857"]


def test_search_empty_q_returns_empty(monkeypatch):
    monkeypatch.setattr(fund_mod.ak, "fund_name_em", _names_df)
    assert fund_mod.search_funds("  ", limit=20) == []
    assert fund_mod.search_funds("", limit=20) == []


def test_search_respects_limit(monkeypatch):
    rows = [
        {
            "基金代码": f"{i:06d}",
            "拼音缩写": f"P{i}",
            "基金简称": f"测试基金{i}",
            "基金类型": "混合型",
            "拼音全称": f"CESHI{i}",
        }
        for i in range(5)
    ]
    monkeypatch.setattr(
        fund_mod.ak, "fund_name_em", lambda: pd.DataFrame(rows)
    )
    items = fund_mod.search_funds("测试", limit=2)
    assert len(items) == 2
```

- [ ] **Step 2: 跑测确认失败**

```bash
cd /Users/orange/Desktop/code/share-data/backend && .venv/bin/python -m pytest tests/test_fund.py -v
```

Expected: FAIL（`app.fund` 不存在或符号未定义）。

- [ ] **Step 3: 实现搜索与缓存**

创建 `backend/app/fund.py`：

```python
"""Open-ended (场外) fund search and detail via AKShare."""

from __future__ import annotations

import time
from typing import Any

import akshare as ak
import pandas as pd

FUND_NAME_CACHE_TTL_SEC = 3600

_name_cache: list[dict[str, str]] | None = None
_name_cache_at: float = 0.0


def clear_fund_name_cache() -> None:
    global _name_cache, _name_cache_at
    _name_cache = None
    _name_cache_at = 0.0


def _load_name_rows() -> list[dict[str, str]]:
    global _name_cache, _name_cache_at
    now = time.monotonic()
    if _name_cache is not None and (now - _name_cache_at) < FUND_NAME_CACHE_TTL_SEC:
        return _name_cache
    df = ak.fund_name_em()
    rows: list[dict[str, str]] = []
    if df is None or df.empty:
        _name_cache = rows
        _name_cache_at = now
        return rows
    for _, r in df.iterrows():
        symbol = str(r.get("基金代码") or "").strip()
        if not symbol:
            continue
        rows.append(
            {
                "symbol": symbol,
                "name": str(r.get("基金简称") or "").strip(),
                "type": str(r.get("基金类型") or "").strip(),
                "pinyin": str(r.get("拼音缩写") or "").strip(),
            }
        )
    _name_cache = rows
    _name_cache_at = now
    return rows


def search_funds(q: str, limit: int = 20) -> list[dict[str, Any]]:
    query = (q or "").strip()
    if not query:
        return []
    lim = max(1, min(int(limit or 20), 50))
    needle = query.casefold()
    out: list[dict[str, Any]] = []
    for row in _load_name_rows():
        symbol = row["symbol"]
        name = row["name"]
        pinyin = row["pinyin"]
        if (
            symbol.startswith(query)
            or needle in name.casefold()
            or pinyin.casefold().startswith(needle)
        ):
            out.append(
                {
                    "symbol": symbol,
                    "name": name,
                    "type": row["type"],
                    "pinyin": pinyin,
                }
            )
            if len(out) >= lim:
                break
    return out
```

- [ ] **Step 4: 跑测确认通过**

```bash
cd /Users/orange/Desktop/code/share-data/backend && .venv/bin/python -m pytest tests/test_fund.py -v
```

Expected: PASS（本 Task 内全部 search 用例）。

- [ ] **Step 5: Commit（仅当用户要求时）**

```bash
git add backend/app/fund.py backend/tests/test_fund.py
git commit -m "feat: add open-ended fund search via AKShare"
```

---

### Task 2: 后端详情聚合 + 单测

**Files:**
- Modify: `backend/app/fund.py`
- Modify: `backend/tests/test_fund.py`

**Interfaces:**
- Produces:
  - `get_fund_detail(symbol: str) -> dict[str, Any]`  
    形状见 spec；非法 symbol 抛 `ValueError`；档案与净值皆空抛 `LookupError`
  - 内部可暴露 `_normalize_symbol(symbol: str) -> str`（可选，测试不必依赖）
- Consumes: `ak.fund_overview_em(symbol=...)`、`ak.fund_open_fund_info_em(symbol=..., indicator="单位净值走势")`

- [ ] **Step 1: 追加失败单测**

在 `test_fund.py` 末尾追加：

```python
def _overview_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "基金全称": "华夏中证电网设备主题交易型开放式指数证券投资基金发起式联接基金",
                "基金简称": "华夏中证电网设备主题ETF发起式联接C",
                "基金代码": "025857（前端）",
                "基金类型": "指数型-股票",
                "发行日期": "2025年10月27日",
                "成立日期/规模": "2025年11月25日 / 4.451亿份",
                "净资产规模": "85.78亿元（截止至：2026年06月30日）",
                "份额规模": "59.9097亿份（截止至：2026年06月30日）",
                "基金管理人": "华夏基金",
                "基金托管人": "招商证券",
                "基金经理人": "单宽之",
                "成立来分红": "每份累计0.00元（0次）",
                "管理费率": "0.50%（每年）",
                "托管费率": "0.10%（每年）",
                "销售服务费率": "0.30%（每年）",
                "最高认购费率": "0.00%（前端）",
                "最高申购费率": "0.00%（前端）",
                "最高赎回费率": "1.50%（前端）",
                "业绩比较基准": "中证电网设备主题指数收益率*95%+人民币活期存款税后利率*5%",
                "跟踪标的": "中证电网设备主题指数",
            }
        ]
    )


def _nav_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"净值日期": "2026-07-22", "单位净值": 1.0960, "日增长率": -2.07},
            {"净值日期": "2026-07-23", "单位净值": 1.1474, "日增长率": 4.69},
            {"净值日期": "2026-07-24", "单位净值": 1.1087, "日增长率": -3.37},
        ]
    )


def test_get_fund_detail_maps_overview_and_nav(monkeypatch):
    monkeypatch.setattr(fund_mod.ak, "fund_overview_em", lambda symbol: _overview_df())
    monkeypatch.setattr(
        fund_mod.ak,
        "fund_open_fund_info_em",
        lambda symbol, indicator="单位净值走势", period="成立来": _nav_df(),
    )
    out = fund_mod.get_fund_detail("025857")
    assert out["symbol"] == "025857"
    assert out["name"] == "华夏中证电网设备主题ETF发起式联接C"
    assert out["overview"]["company"] == "华夏基金"
    assert out["overview"]["fees"]["management"] == "0.50%（每年）"
    assert out["nav"]["latest"]["date"] == "2026-07-24"
    assert out["nav"]["latest"]["nav"] == 1.1087
    assert len(out["nav"]["series"]) == 3


def test_get_fund_detail_invalid_symbol():
    with pytest.raises(ValueError):
        fund_mod.get_fund_detail("25857")
    with pytest.raises(ValueError):
        fund_mod.get_fund_detail("abc")


def test_get_fund_detail_empty_raises_lookup(monkeypatch):
    monkeypatch.setattr(
        fund_mod.ak, "fund_overview_em", lambda symbol: pd.DataFrame()
    )

    def _boom(**_kwargs):
        raise RuntimeError("upstream")

    monkeypatch.setattr(fund_mod.ak, "fund_open_fund_info_em", _boom)
    with pytest.raises(LookupError):
        fund_mod.get_fund_detail("025857")


def test_get_fund_detail_nav_failure_degrades(monkeypatch):
    monkeypatch.setattr(fund_mod.ak, "fund_overview_em", lambda symbol: _overview_df())

    def _boom(**_kwargs):
        raise RuntimeError("nav down")

    monkeypatch.setattr(fund_mod.ak, "fund_open_fund_info_em", _boom)
    out = fund_mod.get_fund_detail("025857")
    assert out["overview"]["manager"] == "单宽之"
    assert out["nav"] is None
    assert "nav_error" in out and out["nav_error"]
```

- [ ] **Step 2: 跑测确认新用例失败**

```bash
cd /Users/orange/Desktop/code/share-data/backend && .venv/bin/python -m pytest tests/test_fund.py -v
```

Expected: 新 `get_fund_detail_*` FAIL。

- [ ] **Step 3: 实现详情聚合**

在 `fund.py` 追加（可按需微调列名容错）：

```python
def _normalize_symbol(symbol: str) -> str:
    clean = "".join(ch for ch in str(symbol or "") if ch.isdigit())
    if len(clean) != 6:
        raise ValueError("基金代码须为 6 位数字")
    return clean


def _cell(row: pd.Series, key: str) -> str:
    if key not in row.index:
        return ""
    v = row[key]
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    return str(v).strip()


def _num(v: Any) -> float | None:
    if v is None or v == "" or v == "-":
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f:
        return None
    return f


def _parse_overview(df: pd.DataFrame) -> tuple[str, dict[str, Any]] | None:
    if df is None or df.empty:
        return None
    row = df.iloc[0]
    name = _cell(row, "基金简称")
    overview = {
        "full_name": _cell(row, "基金全称"),
        "type": _cell(row, "基金类型"),
        "establish_date": _cell(row, "成立日期/规模"),
        "scale": _cell(row, "净资产规模"),
        "manager": _cell(row, "基金经理人"),
        "company": _cell(row, "基金管理人"),
        "custodian": _cell(row, "基金托管人"),
        "benchmark": _cell(row, "业绩比较基准"),
        "tracking": _cell(row, "跟踪标的"),
        "fees": {
            "management": _cell(row, "管理费率"),
            "custody": _cell(row, "托管费率"),
            "sales": _cell(row, "销售服务费率"),
            "subscribe": _cell(row, "最高申购费率") or _cell(row, "最高认购费率"),
            "redeem": _cell(row, "最高赎回费率"),
        },
    }
    return name, overview


def _parse_nav(df: pd.DataFrame) -> dict[str, Any] | None:
    if df is None or df.empty:
        return None
    series: list[dict[str, Any]] = []
    for _, r in df.iterrows():
        date = _cell(r, "净值日期")
        nav = _num(r.get("单位净值") if hasattr(r, "get") else r["单位净值"])
        if not date or nav is None:
            continue
        chg = _num(r.get("日增长率") if hasattr(r, "get") else r["日增长率"])
        series.append({"date": date, "nav": nav, "change_pct": chg})
    if not series:
        return None
    latest = series[-1]
    return {"latest": latest, "series": series}


def get_fund_detail(symbol: str) -> dict[str, Any]:
    sym = _normalize_symbol(symbol)
    overview_name = ""
    overview: dict[str, Any] | None = None
    try:
        ov_df = ak.fund_overview_em(symbol=sym)
        parsed = _parse_overview(ov_df)
        if parsed:
            overview_name, overview = parsed
    except Exception:
        overview = None

    nav: dict[str, Any] | None = None
    nav_error: str | None = None
    try:
        nav_df = ak.fund_open_fund_info_em(
            symbol=sym, indicator="单位净值走势", period="成立来"
        )
        nav = _parse_nav(nav_df)
        if nav is None:
            nav_error = "净值数据为空"
    except Exception as exc:
        nav = None
        nav_error = f"{type(exc).__name__}: {exc}"

    if overview is None and nav is None:
        raise LookupError(f"未找到基金 {sym}")

    name = overview_name or sym
    out: dict[str, Any] = {
        "symbol": sym,
        "name": name,
        "overview": overview,
        "nav": nav,
    }
    if nav is None and nav_error:
        out["nav_error"] = nav_error
    return out
```

注意：`_parse_nav` 中对 `Series` 取值优先用 `r["单位净值"]` / `r["日增长率"]`，与测试 DataFrame 列一致即可。

- [ ] **Step 4: 跑测确认通过**

```bash
cd /Users/orange/Desktop/code/share-data/backend && .venv/bin/python -m pytest tests/test_fund.py -v
```

Expected: 全部 PASS。

- [ ] **Step 5: Commit（仅当用户要求时）**

```bash
git add backend/app/fund.py backend/tests/test_fund.py
git commit -m "feat: aggregate open-ended fund overview and NAV"
```

---

### Task 3: Provider + HTTP 路由

**Files:**
- Modify: `backend/app/providers/akshare_provider.py`
- Modify: `backend/app/main.py`（在 `market` 路由附近追加 fund 路由）
- Modify: `backend/tests/test_fund.py`（可选：加 provider 薄封装测试；HTTP 可用 TestClient 或跳过，优先保证 provider 委托）

**Interfaces:**
- Produces:
  - `AkshareProvider.features` 含 `"fund"`
  - `AkshareProvider.get_fund_search(q: str, limit: int = 20) -> dict`
  - `AkshareProvider.get_fund_detail(symbol: str) -> dict`（响应含 `"source": "akshare"`）
  - `GET /api/{source}/fund/search?q=&limit=`
  - `GET /api/{source}/fund/{symbol}`

- [ ] **Step 1: 扩展 Provider**

在 `akshare_provider.py`：

```python
from .. import fund as fund_service

# features 改为：
features = ("explorer", "market", "kline", "quote", "fund")

def get_fund_search(self, q: str, limit: int = 20) -> dict[str, Any]:
    items = fund_service.search_funds(q, limit=limit)
    return {"source": self.id, "q": (q or "").strip(), "items": items}

def get_fund_detail(self, symbol: str) -> dict[str, Any]:
    detail = fund_service.get_fund_detail(symbol)
    return {"source": self.id, **detail}
```

同步更新文件顶部 docstring：`explorer + market + kline + quote + fund`。

- [ ] **Step 2: 注册路由**

在 `main.py` 的 `market` 路由后追加：

```python
@app.get("/api/{source}/fund/search")
def fund_search(
    source: str,
    q: str = Query(default="", description="代码/简称/拼音"),
    limit: int = Query(default=20, ge=1, le=50),
) -> dict[str, Any]:
    provider = _provider_or_404(source)
    if "fund" not in provider.features:
        raise HTTPException(
            status_code=404,
            detail=f"数据源 {source} 暂不支持基金详情（features={list(provider.features)}）",
        )
    get_search = getattr(provider, "get_fund_search", None)
    if get_search is None:
        raise HTTPException(status_code=404, detail="基金搜索未实现")
    try:
        return get_search(q=q, limit=limit)
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"基金搜索失败: {type(exc).__name__}"
        ) from exc


@app.get("/api/{source}/fund/{symbol}")
def fund_detail(source: str, symbol: str) -> dict[str, Any]:
    provider = _provider_or_404(source)
    if "fund" not in provider.features:
        raise HTTPException(
            status_code=404,
            detail=f"数据源 {source} 暂不支持基金详情（features={list(provider.features)}）",
        )
    get_detail = getattr(provider, "get_fund_detail", None)
    if get_detail is None:
        raise HTTPException(status_code=404, detail="基金详情未实现")
    try:
        return get_detail(symbol=symbol)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"基金详情获取失败: {type(exc).__name__}"
        ) from exc
```

注意：`/fund/search` 必须注册在 `/fund/{symbol}` **之前**，避免 `search` 被当成 symbol。

- [ ] **Step 3: 追加 provider 单测**

```python
def test_provider_fund_wrappers(monkeypatch):
    from app.providers.akshare_provider import AkshareProvider

    monkeypatch.setattr(
        fund_mod, "search_funds", lambda q, limit=20: [{"symbol": "025857", "name": "x", "type": "t", "pinyin": "p"}]
    )
    monkeypatch.setattr(
        fund_mod,
        "get_fund_detail",
        lambda symbol: {
            "symbol": symbol,
            "name": "n",
            "overview": {},
            "nav": None,
            "nav_error": "x",
        },
    )
    p = AkshareProvider()
    assert "fund" in p.features
    assert p.get_fund_search("025", 10)["items"][0]["symbol"] == "025857"
    assert p.get_fund_detail("025857")["source"] == "akshare"
```

- [ ] **Step 4: 跑测**

```bash
cd /Users/orange/Desktop/code/share-data/backend && .venv/bin/python -m pytest tests/test_fund.py -v
```

Expected: PASS。

- [ ] **Step 5: Commit（仅当用户要求时）**

```bash
git add backend/app/providers/akshare_provider.py backend/app/main.py backend/tests/test_fund.py
git commit -m "feat: expose fund search and detail API on AKShare"
```

---

### Task 4: 前端导航与路由脚手架

**Files:**
- Modify: `frontend/src/sources.ts`
- Modify: `frontend/src/components/PageNav.tsx`
- Modify: `frontend/src/main.tsx`
- Create: `frontend/src/FundPage.tsx`（最小占位：顶栏 + 「加载中」文案，后续 Task 填满）

**Interfaces:**
- Produces: `SourceFeature` 含 `'fund'`；`sourcePath(id, 'fund')` → `/{id}/fund`；路由 `/:source/fund`；兼容 `/fund` → `/akshare/fund`

- [ ] **Step 1: 更新 `sources.ts`**

```typescript
export type SourceFeature = 'explorer' | 'market' | 'kline' | 'quote' | 'fund'

// AKShare FALLBACK_SOURCES.features 加入 'fund'

export function sourcePath(sourceId: string, feature: SourceFeature = 'explorer'): string {
  if (feature === 'explorer') return `/${sourceId}`
  if (feature === 'market') return `/${sourceId}/market`
  if (feature === 'kline') return `/${sourceId}/kline`
  if (feature === 'fund') return `/${sourceId}/fund`
  return `/${sourceId}`
}
```

- [ ] **Step 2: 更新 `PageNav.tsx`**

在 K 线 Link 后增加：

```tsx
{hasFeature(current, 'fund') ? (
  <Link
    to={sourcePath(sourceId, 'fund')}
    className={activeFeature === 'fund' ? 'active' : ''}
  >
    基金详情
  </Link>
) : null}
```

- [ ] **Step 3: 占位 `FundPage.tsx` + 路由**

`FundPage.tsx` 最小实现：与 `MarketPage` 相同的 source/feature 守卫（无 `fund` → `<Navigate to={\`/${source}\`} />`），顶栏 `PageNav activeFeature="fund"`，正文临时 `<p>基金详情</p>`。

`main.tsx`：

```tsx
import FundPage from './FundPage'
// ...
<Route path="/fund" element={<Navigate to={`/${DEFAULT_SOURCE}/fund`} replace />} />
<Route path="/:source/fund" element={<FundPage />} />
```

- [ ] **Step 4: 本地确认导航**

```bash
cd /Users/orange/Desktop/code/share-data/frontend && npm run build
```

Expected: 编译通过。手动打开 `/akshare` 应能看到「基金详情」Tab（需后端 `/api/sources` 已返回新 feature；若前端仍用 FALLBACK，也会显示）。

- [ ] **Step 5: Commit（仅当用户要求时）**

```bash
git add frontend/src/sources.ts frontend/src/components/PageNav.tsx frontend/src/main.tsx frontend/src/FundPage.tsx
git commit -m "feat: add fund detail nav tab and route shell"
```

---

### Task 5: `fundApi` 客户端 + 单测

**Files:**
- Create: `frontend/src/fundApi.ts`
- Create: `frontend/src/fundApi.test.ts`

**Interfaces:**
- Produces:
  - Types: `FundSearchItem`, `FundOverview`, `FundNavPoint`, `FundDetailResponse`, `FundSearchResponse`
  - `searchFunds(q: string, source?: string, limit?: number): Promise<FundSearchResponse>`
  - `fetchFundDetail(symbol: string, source?: string): Promise<FundDetailResponse>`
  - `normalizeFundSymbol(raw: string): string`（只保留数字，截断 6 位）
  - `formatNav(n: number | null | undefined): string`
  - `formatNavPct(n: number | null | undefined): string`（带 +/-/%）
  - 复用 `trendClass`：从 `marketApi` re-export 或 FundPage 直接 import

- [ ] **Step 1: 写 `fundApi.ts`**

```typescript
export type FundSearchItem = {
  symbol: string
  name: string
  type: string
  pinyin: string
}

export type FundSearchResponse = {
  source: string
  q: string
  items: FundSearchItem[]
}

export type FundNavPoint = {
  date: string
  nav: number
  change_pct: number | null
}

export type FundOverview = {
  full_name: string
  type: string
  establish_date: string
  scale: string
  manager: string
  company: string
  custodian: string
  benchmark: string
  tracking: string
  fees: {
    management: string
    custody: string
    sales: string
    subscribe: string
    redeem: string
  }
}

export type FundDetailResponse = {
  source: string
  symbol: string
  name: string
  overview: FundOverview | null
  nav: { latest: FundNavPoint; series: FundNavPoint[] } | null
  nav_error?: string
}

export function normalizeFundSymbol(raw: string): string {
  return (raw || '').replace(/\D/g, '').slice(0, 6)
}

export function formatNav(n: number | null | undefined): string {
  if (n === null || n === undefined || Number.isNaN(n)) return '—'
  return n.toFixed(4)
}

export function formatNavPct(n: number | null | undefined): string {
  if (n === null || n === undefined || Number.isNaN(n)) return '—'
  const sign = n > 0 ? '+' : ''
  return `${sign}${n.toFixed(2)}%`
}

export async function searchFunds(
  q: string,
  source = 'akshare',
  limit = 20,
): Promise<FundSearchResponse> {
  const qs = new URLSearchParams({ q, limit: String(limit) })
  const res = await fetch(
    `/api/${encodeURIComponent(source)}/fund/search?${qs}`,
  )
  if (!res.ok) {
    const text = await res.text()
    throw new Error(text || `搜索失败 HTTP ${res.status}`)
  }
  return res.json()
}

export async function fetchFundDetail(
  symbol: string,
  source = 'akshare',
): Promise<FundDetailResponse> {
  const res = await fetch(
    `/api/${encodeURIComponent(source)}/fund/${encodeURIComponent(symbol)}`,
  )
  if (!res.ok) {
    let detail = `详情失败 HTTP ${res.status}`
    try {
      const body = await res.json()
      if (body?.detail) detail = String(body.detail)
    } catch {
      /* ignore */
    }
    throw new Error(detail)
  }
  return res.json()
}
```

错误解析风格可对齐 `klineApi.ts`（若其有 `detail` 提取则复制同一模式）。

- [ ] **Step 2: 写单测**

```typescript
// frontend/src/fundApi.test.ts
import { describe, expect, it } from 'vitest'
import { formatNav, formatNavPct, normalizeFundSymbol } from './fundApi'

describe('fundApi helpers', () => {
  it('normalizes symbol digits', () => {
    expect(normalizeFundSymbol('025857（前端）')).toBe('025857')
    expect(normalizeFundSymbol('25857')).toBe('25857')
  })

  it('formats nav and pct', () => {
    expect(formatNav(1.1087)).toBe('1.1087')
    expect(formatNavPct(-3.37)).toBe('-3.37%')
    expect(formatNavPct(4.69)).toBe('+4.69%')
    expect(formatNav(null)).toBe('—')
  })
})
```

- [ ] **Step 3: 跑测**

```bash
cd /Users/orange/Desktop/code/share-data/frontend && npm test -- fundApi.test.ts
```

Expected: PASS。

- [ ] **Step 4: Commit（仅当用户要求时）**

```bash
git add frontend/src/fundApi.ts frontend/src/fundApi.test.ts
git commit -m "feat: add fund detail API client"
```

---

### Task 6: `FundPage` 完整 UI + 样式 + README

**Files:**
- Modify: `frontend/src/FundPage.tsx`（完整实现）
- Modify: `frontend/src/styles.css`
- Modify: `README.md`

**Interfaces:**
- Consumes: Task 5 的 `searchFunds` / `fetchFundDetail` / helpers；`trendClass` from `marketApi`
- Produces: 可用页面，满足 spec 验收标准 1–4

- [ ] **Step 1: 实现 `FundPage` 行为要点**

对齐 `KlinePage` 的 URL 模式：

```typescript
function readInitial(sp: URLSearchParams) {
  const symbol =
    normalizeFundSymbol(sp.get('symbol') || '025857') || '025857'
  // 若不足 6 位仍用 025857
  return { symbol: symbol.length === 6 ? symbol : '025857' }
}
```

交互：

1. `symbolInput` 受控；防抖 250ms 调 `searchFunds`；结果下拉。
2. 提交：`normalizeFundSymbol` 后若长度 ≠ 6 → `setError('请输入 6 位基金代码')`；否则 `setSearchParams({ symbol }, { replace: true })`。
3. `useEffect` 监听 `searchParams` → 同步 input/symbol → `fetchFundDetail`。
4. 档案区：overview 字段网格；`overview === null` 时显示「暂无档案」。
5. 净值区：`nav` 有值时摘要 + SVG 折线 + 表格（series 倒序展示最近在上亦可，但须前后一致；推荐表格最新在上：`[...series].reverse()`）；`nav_error` 时仅净值区报错。
6. SVG 折线：用 `series` 的 `nav` 映射到 viewBox 宽高，`<polyline>` + 少量轴标签即可；空 series 不渲染图。

页面骨架（结构示意，实现时可拆小组件到同文件）：

```tsx
<header className="topbar">...</header>
<main className="fund-main">
  <form className="fund-search">...</form>
  {error ? <div className="error-banner">{error}</div> : null}
  {loading ? <div className="table-empty">正在拉取基金详情…</div> : null}
  {data ? (
    <>
      <section className="fund-overview">...</section>
      <section className="fund-nav">...</section>
    </>
  ) : null}
</main>
```

- [ ] **Step 2: 样式**

在 `styles.css` 追加（命名与现有 market 一致的间距）：

```css
.fund-main { /* padding 对齐 market-main */ }
.fund-search { /* flex 行：input + button + 相对定位下拉 */ }
.fund-suggest { /* absolute 下拉列表 */ }
.fund-overview-grid { /* CSS grid 2–3 列 */ }
.fund-nav-chart { /* 固定高度 SVG 容器 */ }
.fund-nav-table-wrap { /* max-height + overflow auto */ }
```

涨跌色复用现有 `.up` / `.down`。

- [ ] **Step 3: 更新 README**

在「打开：」列表增加：

```markdown
- 基金详情：<http://127.0.0.1:5173/akshare/fund?symbol=025857>
```

并注明旧路径 `/fund` 重定向到 AKShare。

- [ ] **Step 4: 构建与后端单测回归**

```bash
cd /Users/orange/Desktop/code/share-data/frontend && npm test && npm run build
cd /Users/orange/Desktop/code/share-data/backend && .venv/bin/python -m pytest tests/test_fund.py -v
```

Expected: 全部 PASS；前端 build 成功。

手动验收（后端 + `frontend` dev 已起）：

1. 打开 `/akshare/fund` → 默认 `025857` 档案与净值。
2. 输入「电网」或「HXZZ」→ 联想含 `025857` → 点选后 URL/`详情` 更新。
3. 直接打开 `/akshare/fund?symbol=025857` 深链可用。
4. 输入非法代码有前端提示；可临时断网或 mock 验证净值降级文案。

- [ ] **Step 5: Commit（仅当用户要求时）**

```bash
git add frontend/src/FundPage.tsx frontend/src/styles.css README.md
git commit -m "feat: ship AKShare open-ended fund detail page"
```

---

## Spec coverage checklist

| Spec 要求 | Task |
|-----------|------|
| Tab「基金详情」 | 4 |
| 场外开放式档案 + 净值 | 2, 6 |
| 代码/简称/拼音搜索 | 1, 5, 6 |
| URL `?symbol=` + 默认 025857 | 6 |
| 专用 API + Provider feature | 3 |
| 净值降级 `nav_error` | 2, 6 |
| 非 akshare 引导回 explorer | 4, 6 |
| 后端 mock 单测 | 1, 2, 3 |
| 前端 helper 单测 | 5 |
| README 入口 | 6 |
| 不做 ETF/持仓/重型图表 | 全任务遵守 |

## Plan self-review notes

- 无 TBD/占位步骤；关键签名在 Task 间一致（`search_funds` / `get_fund_detail` / `get_fund_search`）。
- 路由顺序：`/fund/search` 先于 `/fund/{symbol}` 已写明。
- `subscribe` 费率：优先「最高申购费率」，回退「最高认购费率」，与实测东财列一致。
