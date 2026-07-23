# 投委会软删除 Task 2 严格复审

## 结论

**APPROVED**

- **Spec compliance：APPROVED**
- **Code quality：APPROVED**

原审查的两项 Important 发现均已修复。当前 DELETE 路由满足认证、用户隔离、审计字段、404/409 映射及基础设施隔离要求，新增测试也真实覆盖了修复后的生产调用链。

## Critical

无。

## Important

无。

## Minor

无。

## 复审确认

### 认证与用户隔离

- `DELETE /api/advisor/committee/runs/{run_id}` 使用 `Depends(get_current_user)`。
- 认证测试已覆盖 DELETE，并断言未认证请求返回 401。
- 路由从认证用户提取 `uid`，将其同时作为 `soft_delete_run()` 的用户范围参数和 `deleted_by`。
- URL 中的 `run_id` 原样传给仓储，不存在跨用户查询或删除参数。

### 响应与领域错误映射

- 成功响应为 `{"run_id": run_id, "deleted": True}`。
- `RunNotFound` 映射为 HTTP 404。
- `IllegalStatusTransition` 与 `VersionConflict` 映射为 HTTP 409。
- `deleted_at` 使用 `datetime.now(timezone.utc)`，为带时区 UTC 时间。

### Redis、RQ 与 checkpoint 隔离

- 新增 `_plain_repository()` 仅调用 `CommitteeRepository.from_default_database()`。
- `delete_run()` 使用 `_plain_repository()`，不再进入包含 `create_queue()` 和 `reconcile_stale_runs()` 的 `_repository()`。
- DELETE 路径不调用 `_infra()` 或 `initialize_checkpoint_saver()`。
- 原 `_repository()` 的 reconcile 行为保持不变，修复未改变其他路由既有语义。

### 测试真实性

- 用户隔离与错误映射测试已改为替换 `_plain_repository()`，与当前生产结构一致。
- 基础设施隔离测试没有替换 `_plain_repository()`，而是仅替换底层 `CommitteeRepository.from_default_database()` 返回 mock repository，因此仍真实经过 DELETE 的生产 helper。
- 测试把 `create_queue`、`reconcile_stale_runs`、`initialize_checkpoint_saver` 和 `_infra` 设置为调用即失败，并通过真实 HTTP DELETE 请求验证隔离边界。
- 成功路径同时断言仓储软删除确实被调用，避免只验证“未触碰基础设施”而漏掉核心行为。

## 测试说明

按复审要求，未重跑实施报告中记录的测试；本结论基于计划、追加后的实施报告及当前生产代码和测试代码的静态复审。
