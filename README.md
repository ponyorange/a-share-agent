# Share Data Explorer

多数据源行情 / 接口浏览 Web 应用，外加独立的**短期交易顾问**前端。

| 数据源 | 接口浏览器 | 大盘行情 | K 线图 |
|--------|:----------:|:--------:|:------:|
| [AKShare](https://akshare.akfamily.xyz/) | ✓ | ✓ | ✓（含盘口） |
| [Tushare](https://tushare.pro/document/2) | ✓ | — | — |
| [BaoStock](http://baostock.com) | ✓ | — | ✓ |

- **后端**：FastAPI + 可插拔 Provider + 顾问（规则评分 / AKQuant 校验）
- **前端（数据后台）**：`frontend/` · Vite + React · 端口 5173
- **前端（交易顾问）**：`frontend-advisor/` · Vite + React · 端口 5174

## 环境要求

- Python **>= 3.10**（推荐 3.12）
- Node.js **>= 18**

## 启动后端

```bash
cd backend
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

### 环境变量（`.env`）

```bash
cd backend
cp .env.example .env
# 编辑 .env，填入 TUSHARE_TOKEN 等
```

后端启动时会自动加载 `backend/.env`（也可放在仓库根目录 `.env`）。  
`.env` 已在 `.gitignore` 中，请勿提交 Token。

| 变量 | 含义 |
|------|------|
| `TUSHARE_TOKEN` | Tushare Pro Token（调用 Tushare 接口必填） |
| `AKSHARE_USE_SYSTEM_PROXY=1` | 使用系统代理 |
| `AKSHARE_DISABLE_HOST_REWRITE=1` | 关闭东财 host 改写 |

生产启动必须显式设置 `APP_ENV=production`、`MONGODB_URI` 和至少 32 字节的
高熵 `JWT_SECRET`。用户 LLM API Key 使用独立的
`LLM_ENCRYPTION_KEY` 加密；轮换期间可短暂配置
`LLM_ENCRYPTION_KEY_PREVIOUS`，完成重加密后应立即移除。开发种子账户只有
在非 production 且 `DEV_SEED_ENABLED=1` 时创建，用户名和强密码必须分别由
`DEV_SEED_USERNAME`、`DEV_SEED_PASSWORD` 注入，已有账户不会被重置。

健康检查：<http://127.0.0.1:8000/api/health>  
数据源列表：<http://127.0.0.1:8000/api/sources>

### 启用投委会 worker

投委会默认关闭，`COMMITTEE_ENABLED=0` 时旧行情、顾问和模拟盘接口无需
Redis，仍按上述方式启动。启用前需准备外部 MongoDB 和 Redis Stack
（必须包含 RedisJSON、RediSearch），并在本机 `.env` 中填写
`COMMITTEE_ENABLED=1`、`REDIS_HOST`、`REDIS_PORT`、`REDIS_PASSWORD`、
`REDIS_SSL`。模板只保留占位符，真实地址和密码不得写入代码或发布产物。

分别启动 API 与 worker：

```bash
cd backend
source .venv/bin/activate
uvicorn app.main:app --host 0.0.0.0 --port 8000
python -m app.advisor.committee.worker
```

worker 默认以 RQ `with_scheduler=True` 启动，负责把自动恢复产生的
`enqueue_in` 延迟任务从 scheduled registry 移回执行队列；部署时不得另行
关闭该调度器，除非已部署独立 RQ scheduler。

登录后检查 `GET /api/advisor/committee/health`。若 Redis 可连接但缺少
RedisJSON/RediSearch，checkpoint 状态会明确返回
`redis_stack_required`，投委会任务不会降级为不持久的进程内执行。
轮换 Redis 密码时，先在密钥系统或部署机 `.env` 更新密码，再依次重启
worker 和 API，复查健康接口，最后撤销旧密码；不要把新旧密码写进日志。
任何曾在源码、聊天、日志或发布包中出现过的 Redis、Mongo、JWT 与 LLM
加密凭据均视为已暴露，部署前必须在服务端轮换并撤销旧值。

## 启动数据后台前端

```bash
cd frontend
npm install
npm run dev
```

打开：

- AKShare 接口浏览器：<http://127.0.0.1:5173/akshare>
- Tushare 接口浏览器：<http://127.0.0.1:5173/tushare>
- BaoStock 接口浏览器：<http://127.0.0.1:5173/baostock>
- 大盘：<http://127.0.0.1:5173/akshare/market>
- K 线：<http://127.0.0.1:5173/akshare/kline?symbol=000001&range=daily>

旧路径 `/`、`/market`、`/kline` 会重定向到 AKShare。

## 启动短期交易顾问

顾问与数据后台共用同一后端。候选池由 **AKShare** 按成交额动态拉取，分 **ETF / 沪深股 / 科创股** 三板评分；规则因子给出动作，AKQuant 用于校验历史命中率。

```bash
cd frontend-advisor
npm install
npm run dev
```

打开：<http://127.0.0.1:5174/>

| 页面 | 说明 |
|------|------|
| 今日关注 | ETF 白名单 + 持仓中评分靠前的标的 |
| 标的诊断 | 输入代码：无持仓→买/观望；有持仓→卖/持有/加仓 |
| 我的持仓 | 写入 `backend/data/portfolio.json` |
| 策略表现 | 事件研究命中率 + AKQuant 样本回测摘要 |

**免责声明**：输出仅为研究参考，不构成投资建议；次日涨跌无法保证。

评分权重与阈值见 [`backend/app/advisor/config.yaml`](backend/app/advisor/config.yaml)。

## API 约定

```
GET  /api/sources
GET  /api/{source}/categories
GET  /api/{source}/interfaces
GET  /api/{source}/interfaces/{name}
POST /api/{source}/fetch
GET  /api/{source}/market          # 若 source 支持
GET  /api/{source}/kline           # 若 source 支持
GET  /api/{source}/quote           # 盘口：五档 + 分时成交（akshare）

GET  /api/advisor/recommendations?top=15&board=all   # board=etf|hs|star|all
GET  /api/advisor/advice?symbol=510300
GET  /api/advisor/portfolio
POST /api/advisor/portfolio
GET  /api/advisor/portfolio/advice
GET  /api/advisor/backtest/summary?force=false
GET  /api/advisor/universe
```

`board`：`etf` ETF · `hs` 沪深股 · `star` 科创股 · `all` 三板一起。候选池默认来自 AKShare（`fund_etf_spot_em` / `stock_zh_a_spot_em`），股票拉失败时回退东财成交额榜。
`source` 目前为 `akshare` | `tushare` | `baostock`。

兼容旧路径（默认走 akshare）：`/api/categories`、`/api/fetch`、`/api/market`、`/api/kline` 等。

## 扩展新数据源

1. 在 `backend/app/providers/` 新增 Provider（实现 `describe/health/get_categories/list_interfaces/get_interface/fetch`，按需加 `get_market` / `get_kline`）
2. 在 `providers/__init__.py` 注册
3. 前端 `sources.ts` 的 fallback 与功能开关会随 `/api/sources` 自动更新

## 说明

- AKShare：运行时反射枚举公开接口。
- Tushare：按 [官方文档](https://tushare.pro/document/2) 维护的常用接口目录；股票代码为 `ts_code`（如 `600519.SH`）。
- BaoStock：免费免 Token；代码形如 `sh.600519`；调用时自动 login/logout。K 线默认前复权；「实时」为当日 5 分钟线近似。
- 默认单次最多返回 500 行。
- 顾问候选池默认以 ETF 白名单为主（可在持仓中加入个股一并评分）。
