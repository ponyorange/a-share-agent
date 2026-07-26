# AKShare 基金详情 Tab 设计

## 目标

1. 在 AKShare 数据面板顶部功能导航中新增「基金详情」Tab。
2. 支持按基金代码 / 简称 / 拼音搜索场外开放式基金，并展示档案与单位净值走势。
3. 支持 URL 深链：`/akshare/fund?symbol=025857`；无 `symbol` 时默认加载 `025857`。

## 已确认决策

| 项 | 决策 |
|----|------|
| 内容范围 | 档案 + 单位净值走势（表 + 简易折线图） |
| 基金类型 | 仅场外开放式基金（`fund_open_fund_*` / `fund_overview_em`） |
| 查找方式 | 代码输入 + 名称/拼音模糊搜索联想 + URL `?symbol=` |
| 架构 | 专用后端接口 + 独立 `FundPage`（对齐大盘 / K 线） |
| 默认基金 | 无 URL `symbol` 时默认 `025857` |
| 数据源范围 | 仅 AKShare；其它源无 `fund` feature |
| 非目标 | 场内 ETF / 货币基金专用分支、持仓明细、经理履历深挖、重型图表库 |

## 架构

```text
PageNav「基金详情」
        │
        ▼
/:source/fund?symbol=025857
        │
        ▼
FundPage + fundApi.ts
  ├─ GET /api/{source}/fund/search?q=&limit=
  └─ GET /api/{source}/fund/{symbol}
        │
        ▼
AkshareProvider.get_fund_search / get_fund_detail
        │
        ▼
backend/app/fund.py
  ├─ fund_name_em（内存缓存 ~1h）→ 搜索
  ├─ fund_overview_em → 档案
  └─ fund_open_fund_info_em(indicator=单位净值走势) → 净值序列
```

要点：

- 路由、feature、Provider 挂载方式与现有 `market` / `kline` 一致。
- 前端不直接拼通用 `/fetch`；搜索与详情由专用接口返回稳定 JSON。
- `sourcePath` / `SourceFeature` 扩展 `fund`；`sourcePath(..., 'fund')` → `/{source}/fund`。

## 后端 API

### 搜索

`GET /api/{source}/fund/search?q=电网&limit=20`

响应：

```json
{
  "source": "akshare",
  "q": "电网",
  "items": [
    {
      "symbol": "025857",
      "name": "华夏中证电网设备主题ETF发起式联接C",
      "type": "指数型-股票",
      "pinyin": "HXZZDWSBZTETFFQSLJC"
    }
  ]
}
```

规则：

- 匹配：基金代码前缀、简称包含、拼音缩写前缀（大小写不敏感）。
- `fund_name_em` 全量名单进程内缓存约 1 小时。
- `q` 为空或去空白后长度小于 1 → `items: []`。
- `limit` 默认 20，上限 50。
- 非 akshare 或不支持 `fund` feature → 404。

### 详情

`GET /api/{source}/fund/{symbol}`

响应：

```json
{
  "source": "akshare",
  "symbol": "025857",
  "name": "华夏中证电网设备主题ETF发起式联接C",
  "overview": {
    "full_name": "华夏中证电网设备主题交易型开放式指数证券投资基金发起式联接基金",
    "type": "指数型-股票",
    "establish_date": "2025年11月25日 / 4.451亿份",
    "scale": "85.78亿元（截止至：2026年06月30日）",
    "manager": "单宽之",
    "company": "华夏基金",
    "custodian": "招商证券",
    "benchmark": "中证电网设备主题指数收益率*95%+人民币活期存款税后利率*5%",
    "tracking": "中证电网设备主题指数",
    "fees": {
      "management": "0.50%（每年）",
      "custody": "0.10%（每年）",
      "sales": "0.30%（每年）",
      "subscribe": "0.00%（前端）",
      "redeem": "1.50%（前端）"
    }
  },
  "nav": {
    "latest": { "date": "2026-07-24", "nav": 1.1087, "change_pct": -3.37 },
    "series": [
      { "date": "2025-11-25", "nav": 1.0, "change_pct": null }
    ]
  }
}
```

规则：

- `symbol` 须为 6 位数字；否则 400。
- 查无或上游返回空档案且空净值 → 404。
- 上游网络/解析失败 → 502。
- 档案成功、净值失败时：返回 `overview`，`nav` 可为 `null` 并附带 `nav_error` 字符串（前端净值区单独提示）。
- 字段从 `fund_overview_em` 中文列名映射到上述英文键；无法映射的列忽略，不阻塞主流程。

### Provider 与路由挂载

- `AkshareProvider.features` 增加 `"fund"`。
- 方法：`get_fund_search(q, limit)`、`get_fund_detail(symbol)`，内部委托 `app.fund`。
- `main.py` 增加：
  - `GET /api/{source}/fund/search`
  - `GET /api/{source}/fund/{symbol}`
- feature 检查模式与 `/market`、`/kline` 相同。

## 前端

### 路由与导航

- `main.tsx`：`/:source/fund` → `FundPage`；可选兼容重定向 `/fund` → `/akshare/fund`。
- `PageNav`：当 source 具备 `fund` feature 时显示「基金详情」。
- `sources.ts`：`SourceFeature` 增加 `'fund'`；AKShare fallback features 包含 `fund`；`sourcePath` 支持 `fund`。

### 页面结构（`FundPage.tsx`）

1. 顶栏：`PageNav`，`activeFeature="fund"`。
2. 查询区：搜索输入 +「查询」按钮。
   - 输入防抖约 250ms 调搜索接口，下拉最多 20 条。
   - 回车 / 点选结果 / 点击查询：规范化为 6 位代码后写入 URL 并加载详情。
   - 6 位纯数字可直接查询，不强制先选下拉项。
3. 档案区：简称 + 代码标题；类型、成立日/规模、净资产、经理、公司、托管、基准、跟踪标的、费率网格。
4. 净值区：最新净值摘要（日期 / 单位净值 / 日涨跌幅，涨跌着色）；简易折线图（净值 vs 日期，轻量 canvas/SVG，不新增重型图表依赖）；可滚动表格（日期、单位净值、日增长率）。

### URL 同步

- 查询成功：`replace` 更新 `?symbol=`。
- 前进/后退：按 URL 重新加载。
- 初始：`readInitial` 与 K 线页类似；缺省 `symbol` → `025857`。

### 客户端模块

- `fundApi.ts`：`searchFunds`、`fetchFundDetail` 及类型定义。
- 样式复用现有 `styles.css` 中 market/kline 的间距与表格模式，按需增加少量 `fund-*` 类。

## 错误处理

| 场景 | 行为 |
|------|------|
| 搜索无结果 | 下拉提示「无匹配基金」 |
| 代码非法（非 6 位） | 前端拦截；后端 400 |
| 基金不存在 / 拉空 | 404，页面错误文案 |
| AKShare/网络失败 | 502，可重试提示 |
| 档案成功、净值失败 | 档案照常展示，净值区单独报错 |
| 非 akshare 打开 `/fund` | feature 检查后引导回 explorer（与 market 一致） |

## 测试

- 后端（mock AKShare）：
  - 搜索：代码前缀 / 简称包含 / 拼音前缀；空 `q`；limit。
  - 详情：合法 symbol 字段映射；非法 symbol → 400；空数据 → 404。
- 前端：
  - `fundApi` 响应解析。
  - URL `symbol` 读写（若现有 kline 有同类单测则对齐风格）。

## 文件清单（预期）

| 路径 | 变更 |
|------|------|
| `backend/app/fund.py` | 新增：搜索缓存、详情聚合 |
| `backend/app/main.py` | 注册 fund 路由 |
| `backend/app/providers/akshare_provider.py` | feature + getter |
| `backend/tests/test_fund.py` | 后端单测 |
| `frontend/src/FundPage.tsx` | 新页面 |
| `frontend/src/fundApi.ts` | API 客户端 |
| `frontend/src/fundApi.test.ts` | 可选解析单测 |
| `frontend/src/main.tsx` | 路由 |
| `frontend/src/components/PageNav.tsx` | Tab |
| `frontend/src/sources.ts` | feature / path |
| `frontend/src/styles.css` | 少量样式 |
| `README.md` | 补充基金详情入口说明 |

## 验收标准

1. AKShare 面板可见「基金详情」Tab；打开 `/akshare/fund` 默认展示 `025857` 档案与净值。
2. 输入「电网」或「HXZZ」可在联想中看到 `025857`；点选后 URL 变为 `?symbol=025857` 并刷新详情。
3. 深链 `/akshare/fund?symbol=025857` 直接打开对应详情。
4. 非法代码与上游失败有明确错误提示；档案/净值部分失败时互不拖垮整页。
