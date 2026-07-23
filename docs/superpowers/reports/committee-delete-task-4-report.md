# 投委会软删除 Task 4 实施报告

## 结果

工作台删除交互已按计划完成。仅终态会议显示“删除记录”；确认使用固定原生文案；取消不请求；成功后中止旧请求、清空当前详情、刷新历史并自动选择第一条；删除最后一条后显示空态；失败时保留当前详情并显示错误。

未修改后端、`RunHistory`，未实现批量删除或恢复。工作区无 Git，未执行 commit。

## TDD：RED

先仅修改 `CommitteePage.test.tsx`，加入 `deleteCommitteeRun` mock、默认成功响应和五个交互测试，再逐项运行：

- `仅终态会议显示删除记录且取消确认不发请求`：FAIL，找不到“删除记录”按钮。
- `进行中的会议不显示删除记录`：PASS。该用例是负向显示范围约束，在现有 UI 缺失时天然已满足，无法产生有意义的 RED。
- `确认删除后刷新历史并自动选择下一条`：FAIL，找不到“删除记录”按钮。
- `删除最后一条后显示空状态`：FAIL，找不到“删除记录”按钮。
- `删除失败时保留当前详情并显示错误`：FAIL，找不到“删除记录”按钮。

首次并行命令因共享终端工作目录竞争误在仓库根目录执行，报缺少根级 `package.json`；随后改用显式 `cd frontend-advisor`，以上结果均来自有效重跑。

## TDD：GREEN

实现最小生产代码与样式后运行：

```text
npm test -- --run src/committee/CommitteePage.test.tsx
Test Files  1 passed (1)
Tests       31 passed (31)
```

最终指定验证：

```text
npm test -- --run src/committee/CommitteePage.test.tsx src/committee/committeeApi.test.ts
Test Files  2 passed (2)
Tests       48 passed (48)
```

```text
npm run build
退出码 0；tsc -b 与 vite build 成功
```

```text
npm run lint
退出码 0；0 errors，3 warnings
```

## 修改文件

- `frontend-advisor/src/committee/CommitteePage.test.tsx`
  - 增加删除 API mock 与默认响应。
  - 增加终态显示/取消、活跃态隐藏、成功自动选择、空列表、失败保留详情测试。
- `frontend-advisor/src/committee/CommitteePage.tsx`
  - 导入并调用 `deleteCommitteeRun(runId, signal)`。
  - 增加删除确认、pending/错误处理、旧流与详情请求中止、状态清理、历史刷新和首条自动选择。
  - 仅在 `completed`、`failed`、`cancelled` 终态渲染删除入口。
- `frontend-advisor/src/styles.css`
  - 增加删除危险操作颜色、边框和 hover 样式。
- `docs/superpowers/reports/committee-delete-task-4-report.md`
  - 本报告。

## 自检

- [x] 仅终态显示删除入口。
- [x] 原生确认文案严格为“只会从历史列表隐藏此会议，不会撤销审批或订单。确认删除？”。
- [x] 取消确认不调用删除 API。
- [x] 确认后调用 `deleteCommitteeRun(runId, AbortSignal)`。
- [x] 成功后中止旧 stream/detail 请求并清理刷新定时器。
- [x] 成功后清空选中项、详情、工件、时间线、审批弹窗和流状态。
- [x] 刷新历史后自动选择第一条。
- [x] 空历史显示“选择或发起一次会议”空态。
- [x] 删除失败保留详情并通过 alert 显示错误。
- [x] CommitteePage 与 committeeApi 指定测试通过。
- [x] build 成功。
- [x] lint 无错误。
- [x] 未修改后端或 `RunHistory`，未增加批量/恢复能力。

## Concerns

1. `npm run build` 成功，但 Vite 报现有主 JS chunk `559.43 kB` 超过 `500 kB` 的体积警告；与本次删除交互无直接关系。
2. `npm run lint` 退出码为 0，但在未修改的 `StrategyPage.tsx`（2 条）和 `RecommendationsPage.tsx`（1 条）仍有 `unicorn(no-useless-fallback-in-spread)` warning。
3. “进行中的会议不显示删除记录”是负向约束，在按钮尚未实现时即通过；其余四个新增行为均已观察到因 UI 缺失产生的有效 RED。

---

## 审查修复（2026-07-23）

### 审查项核验

审查指出的竞态可以稳定复现：DELETE 成功后等待 `listCommitteeRuns()` 刷新期间，用户选择 `run-2` 会中止删除 action controller 并推进 selection generation；旧 `remove()` continuation 在刷新返回后仍无条件选择权威列表第一条 `run-3`，覆盖用户选择。刷新失败时，原实现也确实会保留已经删除的 `run-1` 历史项。

### 修复前测试（RED）

先新增审查要求的回归测试，再修改生产代码，逐项运行：

- `删除刷新期间用户切换后旧流程不夺回选择并中止删除信号`：FAIL。deferred 刷新返回后，`getCommitteeRun` 最后一次调用由期望的 `run-2` 变成了 `run-3`；测试同时在刷新期间验证 DELETE 使用的 `AbortSignal.aborted === true`。
- `删除成功但刷新失败时移除本地记录且不自动选择`：FAIL。刷新报错后历史列表仍包含已删除的 `run-1`。
- `双击删除只发出一次请求`：PASS。现有 `actionPending` 与 disabled 按钮已经满足重复点击约束；新增测试把该保证固化为回归证据。
- `failed/cancelled 终态会议显示删除记录`：2 个参数化用例均 PASS。现有 `TERMINAL` 渲染条件已经覆盖这两个终态；新增测试补齐证据范围。

### 最小实现修复

`frontend-advisor/src/committee/CommitteePage.tsx` 的删除成功分支现在：

1. 在清空选择时保存 `const generation = ++selectionRef.current`。
2. 立即执行 `setRuns(current => current.filter(...))`，在权威刷新失败时也不会保留已删除记录。
3. 仍调用 `refreshRuns()` 获取服务端权威列表。
4. 刷新 continuation 仅在以下条件全部成立时自动选择第一条：
   - 删除 controller 未中止；
   - 页面仍挂载；
   - 保存的 generation 仍等于当前 selection generation；
   - `selectedIdRef.current == null`，即刷新期间用户没有新选择；
   - 权威列表存在第一条记录。

因此用户在刷新期间选择其他会议时，`selectRun()` 会中止 DELETE signal、推进 generation，旧 continuation 无法夺回选择。刷新失败时保留本地过滤后的历史列表、显示刷新错误、保持空详情，不把失败结果用于自动选择。

### 修复后测试（GREEN）

定向回归：

```text
npm test -- --run src/committee/CommitteePage.test.tsx \
  -t '删除刷新期间用户切换后旧流程不夺回选择并中止删除信号|删除成功但刷新失败时移除本地记录且不自动选择'
Test Files  1 passed (1)
Tests       2 passed | 34 skipped (36)
```

最终指定验证：

```text
npm test -- --run src/committee/CommitteePage.test.tsx src/committee/committeeApi.test.ts
Test Files  2 passed (2)
Tests       53 passed (53)
```

```text
npm run build
退出码 0；tsc -b 与 vite build 成功
```

```text
npm run lint
退出码 0；0 errors，3 warnings
```

### 本轮修改文件

- `frontend-advisor/src/committee/CommitteePage.test.tsx`
  - 增加 deferred 列表刷新竞态与 DELETE signal 中止测试。
  - 增加刷新失败后的本地历史一致性和不自动选择测试。
  - 增加双击只发一次 DELETE 测试。
  - 增加 `failed`、`cancelled` 终态入口参数化测试。
- `frontend-advisor/src/committee/CommitteePage.tsx`
  - 增加删除后本地历史过滤。
  - 增加 controller、generation、当前选择三重 continuation 防护。
- `docs/superpowers/reports/committee-delete-task-4-report.md`
  - 追加本节审查修复与测试证据。

未修改后端、`RunHistory` 或 API 接口；工作区无 Git，未 commit。

### 本轮 concerns

1. build 仍有主 JS chunk `559.52 kB` 超过 `500 kB` 的 Vite 体积警告。
2. lint 仍有 3 条位于未修改页面文件的 `unicorn(no-useless-fallback-in-spread)` warning；退出码为 0，无错误。
3. 双击与新增终态入口测试在修复前即通过，属于对既有实现的证据补齐；本轮有效 RED 来自竞态覆盖和刷新失败后的陈旧历史覆盖。

---

## 第二次复审修复（2026-07-23）

### 根因

此前只在 DELETE 成功分支对当前 `runs` 做一次本地过滤。`refreshRuns()` 对每个列表响应仍直接执行 `setRuns(result.runs)` 并原样返回，因此删除前已发出的旧请求如果最后返回包含 `deletedId` 的快照，会覆盖本地过滤结果并复活已删除历史项。删除 continuation 的 controller/generation/当前选择防护只约束自动选择，无法约束其他 `refreshRuns()` 调用写入列表。

### 回归测试与 RED

先新增 `删除前旧列表请求乱序返回时不会复活已删除记录`：

1. 初始加载活跃 `run-live`。
2. 通过终态 SSE 事件制造删除前已在途的 deferred `listCommitteeRuns()`。
3. DELETE 成功后让删除所属权威刷新先返回 `[run-2]`，确认自动选择 `run-2` 且历史中无 `run-live`。
4. 最后让旧请求返回 `[run-live, run-2, run-3]`。
5. 等待旧响应中的 `run-3` 出现在历史，证明该响应已完成写入路径，再断言 `run-live` 仍不存在且当前详情仍为 `run-2`。

修复前测试稳定 FAIL：旧响应完成后历史重新出现 `run-live`。初次 RED 同时暴露 React `act(...)` warning；测试使用 `act` 包裹 SSE 事件和 deferred resolve 后重跑，得到无 warning、仅因已删除记录复活而失败的有效 RED。

```text
npm test -- --run src/committee/CommitteePage.test.tsx \
  -t '删除前旧列表请求乱序返回时不会复活已删除记录'
Test Files  1 failed (1)
Tests       1 failed | 37 skipped (38)
失败原因：历史中重新出现 run-live
```

### 最小修复

在 `CommitteePage` 会话内增加 `deletedRunIds = useRef(new Set<string>())`：

- `deleteCommitteeRun()` 成功解析且页面仍挂载后，先把 `deletedId` 加入 tombstone Set，并立即过滤当前本地列表。
- tombstone 写入位于 controller aborted 分支之前；即 DELETE 已成功但用户同时切换选择时，后续陈旧响应也不能复活该记录。
- DELETE 抛错时控制流直接进入 `catch`，不会写入 tombstone，失败删除不会隐藏记录。
- `refreshRuns()` 对每一个成功响应先过滤 tombstone，再将同一份 `visibleRuns` 用于 `setRuns` 和返回值。删除前旧响应、删除所属新响应及后续会话内刷新均无法重新引入已成功删除的 ID。

### GREEN 与最终验证

定向回归：

```text
npm test -- --run src/committee/CommitteePage.test.tsx \
  -t '删除前旧列表请求乱序返回时不会复活已删除记录'
Test Files  1 passed (1)
Tests       1 passed | 37 skipped (38)
```

最终指定验证：

```text
npm test -- --run src/committee/CommitteePage.test.tsx src/committee/committeeApi.test.ts
Test Files  2 passed (2)
Tests       55 passed (55)
```

```text
npm run build
退出码 0；tsc -b 与 vite build 成功
```

```text
npm run lint
退出码 0；0 errors，3 warnings
```

### 本轮修改文件

- `frontend-advisor/src/committee/CommitteePage.test.tsx`
  - 增加删除前旧列表请求乱序返回的 deferred 回归测试。
  - 使用 `act` 驱动测试中的 SSE 与 deferred 状态更新。
- `frontend-advisor/src/committee/CommitteePage.tsx`
  - 增加 session 内删除 tombstone Set。
  - 所有列表响应在写入状态和返回前统一过滤 tombstone。
  - DELETE 成功后、任何 aborted continuation 判断前记录 tombstone；失败路径不记录。
- `docs/superpowers/reports/committee-delete-task-4-report.md`
  - 追加本节第二次复审修复与测试证据。

未修改后端、`RunHistory` 或 API 接口；工作区无 Git，未 commit。

### 本轮 concerns

1. build 成功，但主 JS chunk `559.60 kB` 仍超过 Vite `500 kB` 警告阈值。
2. lint 退出码为 0、无错误；未修改的 `RecommendationsPage.tsx` 和 `StrategyPage.tsx` 仍有合计 3 条 warning。

---

## 整分支审查修复（DELETE 在途锁定）

### 根因

`selectRun()` 会无条件 `actionAbort.current?.abort()`。DELETE HTTP 仍在途时若用户切换历史，浏览器可能在服务端已软删除后中止响应读取；catch 忽略 AbortError，既不写 tombstone 也不收敛列表，造成客户端与服务端不一致。

### RED

新增 `删除请求在途时禁止切换历史且不中止删除信号`：deferred DELETE 期间断言历史项与“发起会议”禁用、点击其他会议不改变选择且 `AbortSignal.aborted === false`；DELETE resolve 后自动选择下一条并隐藏已删记录。

### 修复

- 增加 `deleteInFlight` 状态 + ref 同步锁。
- DELETE 发出前上锁；成功写入 tombstone 后立即解锁，以保留“刷新期间可切换并中止 continuation”的既有行为；失败/结束在 `finally` 解锁。
- `selectRun` 在锁定期直接 return。
- `RunHistory` 新增 `selectionLocked`，禁用历史按钮。
- “发起会议”在锁定期禁用。

### GREEN

```text
npm test -- --run src/committee/CommitteePage.test.tsx src/committee/committeeApi.test.ts
```
