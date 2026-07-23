# 投委会软删除 Task 5 实施报告

## 结果

全量回归验证已完成。后端与前端自动化测试、改动文件 IDE lint、前端 lint/build 均通过。浏览器端到端冒烟因当前会话无可用登录态未在本机替用户完成点击验证；DELETE HTTP 契约与仓储软删除审计保留已由既有自动化测试覆盖。

工作区无 Git，未执行 commit。未修改生产代码。

## Step 1：全量后端测试

```text
cd backend
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q
314 passed, 2 warnings in 6.22s
```

警告均为既有第三方弃用：
- `passlib` → `crypt` DeprecationWarning
- Starlette/`httpx` TestClient deprecation

与本计划改动无关。

## Step 2：全量前端测试

```text
cd frontend-advisor
npm test -- --run
Test Files  4 passed (4)
Tests       59 passed (59)
```

## Step 3：改动文件 lint

IDE `ReadLints` 检查：

- `backend/app/advisor/committee/models.py`
- `backend/app/advisor/committee/repository.py`
- `backend/app/advisor/committee/routes.py`
- `frontend-advisor/src/committee/committeeApi.ts`
- `frontend-advisor/src/committee/CommitteePage.tsx`
- `frontend-advisor/src/styles.css`

结果：无新增诊断。

额外前端工具链：

```text
npm run lint
退出码 0；0 errors，3 warnings
（均位于未改动的 StrategyPage.tsx / RecommendationsPage.tsx）

npm run build
退出码 0；tsc -b 与 vite build 成功
主 JS chunk 559.60 kB 超过 Vite 500 kB 体积警告（既有问题）
```

## Step 4：本地 API / UI 冒烟

运行态探测：

- `http://127.0.0.1:5174/` → 200（frontend-advisor 可用）
- `http://127.0.0.1:8000/docs` → 200（后端可用）
- 未认证访问 ` /api/advisor/committee/runs` → 非 200（需登录）

本协调器未持有用户浏览器登录态，因此未替用户完成计划中的点击确认/Mongo 现场核对。对应契约已由自动化覆盖：

- 仓储：`test_soft_delete_terminal_run_hides_it_but_preserves_audit_rows` 等
- HTTP：`test_http_delete_run_*`（404/409/用户隔离/不触达 RQ 基础设施）
- UI：Task 4 终态删除、确认文案、自动选择、空态、失败保留、tombstone 乱序复活防护

建议用户本地登录后按计划 Step 4 清单点验一次；若 DeepSeek API Key 仍因轮换解密失败，需先在设置中重新保存密钥后再发起真实会议。

## Step 5：Git 检查点

工作区仍不是 Git 仓库，跳过 `git status` / `git log`。检查点以本报告与既有 Task 1–4 报告/审查文件为准。

## 自检

- [x] 全量后端测试 PASS
- [x] 全量前端测试 PASS
- [x] 改动文件无新增 lint 诊断
- [x] 前端 lint/build 无错误
- [ ] 浏览器点击冒烟（需用户登录态）——未完成，见 Concerns
- [x] 无生产代码改动

## Concerns

1. 浏览器端到端冒烟未执行；自动化已覆盖契约，但仍建议用户登录后点验删除确认与 Mongo 审计字段。
2. 前端主 chunk 体积警告与 3 条既有 lint warning 仍在，与本次删除功能无关。
3. 若 LLM API Key 仍无法解密，真实会议发起会继续失败；与软删除交付无关，但会影响“发起会议再删除”的完整手测路径——可用既有 `failed`/`cancelled`/`completed` 记录验证删除。
