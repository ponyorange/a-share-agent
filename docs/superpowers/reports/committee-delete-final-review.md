# 投委会软删除整分支合并复审

## Strengths

- 后端领域约束、原子软删除、用户隔离、普通查询隐藏及审计数据保留均符合设计；DELETE 的 404/409 契约和基础设施隔离已有测试覆盖。
- 前端仅允许终态会议删除，确认文案、失败保留、成功刷新、空列表、自动选择、重复点击和迟到列表响应均有回归覆盖。
- 原 Important 已修复：DELETE 发出前同步设置 `deleteInFlightRef`，`selectRun()` 在锁定期间直接返回；历史按钮和“发起会议”按钮同时禁用，因此历史切换不会再中止在途 DELETE。
- DELETE 成功后先写入 `deletedRunIds` 并从当前列表移除，再解除选择锁；后续刷新期间仍保留原有的代际校验，允许用户切换且不会被旧删除流程夺回选择。
- 新增 deferred DELETE 测试 `删除请求在途时禁止切换历史且不中止删除信号`，明确验证历史项与发起入口禁用、删除信号未中止、当前选择不变，以及成功响应后隐藏被删记录并选择下一条。
- 本次复审新鲜验证：CommitteePage 与 committeeApi 共 55 项测试通过，生产构建成功，lint 无错误（仅有 3 条与本改动无关的既有 warning）。

## Issues

### Critical

无。

### Important

无。

### Minor

1. `committeeApi.test.ts` 仍未直接断言可选 `AbortSignal` 最终传给 `fetch`，也未断言删除响应返回值；页面测试已覆盖调用时传入信号，生产实现简单，不阻塞合并。
2. 尚未完成带登录态的浏览器与 Mongo 现场 smoke。自动化已覆盖仓储、HTTP 与 UI 核心契约，可作为发布前人工点验项。

## Assessment

原先阻塞合并的 DELETE 在途切换竞态已被同步 ref 锁、界面禁用和专门的 deferred 回归测试完整覆盖。修复保持了删除成功后刷新期间可切换且旧流程不夺回选择的既有行为，没有发现新的 Critical 或 Important 问题。

剩余两项均为非阻塞的测试/发布验证增强，代码已具备合并条件。

**Merge readiness: READY_WITH_MINORS**
