# Share Data Explorer

多数据源行情 / 接口浏览 Web 应用，外加独立的**短期交易顾问**前端。

| 数据源 | 接口浏览器 | 大盘行情 | K 线图 | 打板 |
|--------|:----------:|:--------:|:------:|:----:|
| [AKShare](https://akshare.akfamily.xyz/) | ✓ | ✓ | ✓（含盘口） | ✓ |
| [Tushare](https://tushare.pro/document/2) | ✓ | — | — | — |
| [BaoStock](http://baostock.com) | ✓ | — | ✓ | — |

- **后端**：FastAPI + 可插拔 Provider + 顾问（规则评分 / AKQuant 校验）
- **前端（数据后台）**：`frontend/` · Vite + React · 端口 5173
- **前端（交易顾问）**：`frontend-advisor/` · Vite + React · 端口 5174

## 环境要求

- Python **>= 3.10**（推荐 3.12）
- Node.js **>= 18**

## Docker 发布包

从仓库根目录运行 `./scripts/package-docker.sh` 会在 `dist/` 生成离线发布包，
包含应用镜像、沙箱 Controller 镜像、沙箱 Runner 镜像、`docker-compose.yml`、
`.env.example` 和 `load-and-run.sh`。部署机复制整个 `dist/` 后执行：

```bash
cd /opt/share-data
./load-and-run.sh
```

首次运行会生成 `.env` 并退出；填入 `MONGODB_URI`、`JWT_SECRET`、
`LLM_ENCRYPTION_KEY` 和至少 32 字节的 `SANDBOX_TOKEN` 后再次运行脚本。
发布包 compose 保持 API 无 Docker socket、Controller 仅在 Compose 内网
`expose: 8090`、Runner 只由 Controller 拉起为一次性容器。

Agent 精读网页（`fetch_url`）在困难页面会自动升级到 Scrapling / 无头浏览器；
主镜像 `share-data:amd64` 已含浏览器依赖。本地开发未安装浏览器时，增强抓取的
浏览器级（L3）会自动跳过，不影响 httpx / Scrapling HTTP 路径。

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
| `MAIL_HOST` / `MAIL_PORT` | 可选 SMTP（默认 `smtp.163.com` / `465`） |
| `MAIL_USER` / `MAIL_PASS` / `MAIL_FROM` | 发件账号；`MAIL_PASS` 为 163 **授权码**（非登录密码） |

### 邮件（可选）

配置 `MAIL_*` 后可用于：个人资料页绑定邮箱验证码、登录页忘记密码、Agent 将聊天摘要发到已验证邮箱，以及盯盘定时任务告警。  
163 授权码：邮箱设置 → POP3/SMTP/IMAP → 开启 SMTP → 新增授权码。真实密钥只放本机/部署机 `.env`，勿提交。  
前端入口：顶栏用户名 → `/account`；登录页「忘记密码」；Agent 面板「定时任务」页 `/agent/jobs`。

盯盘 worker（与主应用同镜像）：本地 `python -m app.advisor.monitor.worker`；Docker Compose 服务名为 `monitor-worker`。  
**须保持常开**（含盘前/盘后/节假日）：负责按 `next_run_at` 激活「明天盯盘」「定点 9:00」等调度，并在交易时段内对已激活的盯盘任务做规则/LLM 求值与邮件告警（不下单）；同进程兼跑模拟盘全自动交易员（`paper_trader`，可自动下模拟单）。仅在盘中启动会错过夜间创建后的次日激活。

生产启动必须显式设置 `APP_ENV=production`、`MONGODB_URI` 和至少 32 字节的
高熵 `JWT_SECRET`。用户 LLM API Key 使用独立的
`LLM_ENCRYPTION_KEY` 加密；轮换期间可短暂配置
`LLM_ENCRYPTION_KEY_PREVIOUS`，完成重加密后应立即移除。开发种子账户只有
在非 production 且 `DEV_SEED_ENABLED=1` 时创建，用户名和强密码必须分别由
`DEV_SEED_USERNAME`、`DEV_SEED_PASSWORD` 注入，已有账户不会被重置。

健康检查：<http://127.0.0.1:8000/api/health>  
数据源列表：<http://127.0.0.1:8000/api/sources>

### 数据 Agent

主顾问在识别到 Provider 外部数据查询或跨源计算需求时，会自动委派给数据
Agent，不需要用户指定底层接口名。数据 Agent 会从运行时 Provider 目录动态
发现 AKShare、Tushare、BaoStock 以及后续注册的新 Provider；新增 Provider
完成后端注册后即可进入发现流程，无需为数据 Agent 单独维护工具清单。
Tushare 调用仍需在 `.env` 中配置 `TUSHARE_TOKEN`。

数据 Agent 只读运行，不写业务数据，也不持久化请求内临时数据。外部查询和
沙箱计算受默认预算保护：单次最多 5,000 行、请求累计最多 50,000 行、输入
最多 50 MiB、沙箱最多 30 秒 / 512 MiB、Python 重试最多 2 次、输出最多
1 MiB，Agent 默认最多 24 步。完整数据只通过请求内 dataset ID 进入沙箱，
不放入主 Agent 上下文。最终回答会说明使用的数据来源、数据时间、计算步骤、
截断或预算限制，以及 Provider 部分失败和口径差异。

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
- 基金详情：<http://127.0.0.1:5173/akshare/fund?symbol=025857>
- 打板：<http://127.0.0.1:5173/akshare/limitup>

旧路径 `/`、`/market`、`/kline`、`/fund`、`/limitup` 会重定向到 AKShare。

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
| 股票诊断 | 输入代码：无持仓→买/观望；有持仓→卖/持有/加仓 |
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
GET  /api/{source}/fund/search     # 场外基金搜索（akshare）
GET  /api/{source}/fund/{symbol}   # 场外基金档案 + 净值（akshare）

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

```
version: "3.8"

services:
  share-data:
    # 直接使用本地已 load 的镜像
    image: share-data:amd64
    pull_policy: never
    container_name: share-data-app
    restart: always
    ports:
      - "8000:8000"

    # 线上秘钥文件（服务器本地文件，不进镜像）
    env_file:
      - .env

    environment:
      - STATIC_ROOT=/app/static
      - PORT=8000
      - CORS_ORIGINS=*
```