# 投委会软删除 Task 2 实施报告

## 结论

已按计划实现认证 DELETE HTTP API：

- 路径：`DELETE /api/advisor/committee/runs/{run_id}`
- 成功响应：`{"run_id": "<run_id>", "deleted": true}`
- 使用认证用户 ID 同时作为 `user_id` 与 `deleted_by`
- 使用带时区的 UTC 时间作为 `deleted_at`
- `RunNotFound` 映射为 HTTP 404
- `IllegalStatusTransition`、`VersionConflict` 映射为 HTTP 409
- 路由不调用 `_infra()`，不会访问 Redis、RQ 或 checkpoint

## RED

严格先修改测试，未预先修改生产代码。

1. 将 DELETE 请求加入 `test_http_committee_routes_require_authentication` 后运行：

   `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/test_committee_task5_review.py::test_http_committee_routes_require_authentication -q`

   结果：`1 failed, 2 warnings`。DELETE 返回 405，预期 401；失败原因正是路由缺失。

2. 添加成功路径、用户隔离、删除审计参数和领域错误映射测试后运行：

   `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/test_committee_task5_review.py::test_http_delete_run_is_user_scoped_and_maps_domain_errors tests/test_committee_task5_review.py::test_http_delete_run_maps_errors -q`

   结果：`4 failed, 2 warnings`。四个用例均因 DELETE 路由缺失返回 405，符合预期 RED。

## GREEN

在 `get_run` 与 `cancel_run` 之间添加最小 `delete_run` 路由，仅调用既有的 `CommitteeRepository.soft_delete_run(...)`。

聚焦验证：

`PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/test_committee_task5_review.py::test_http_committee_routes_require_authentication tests/test_committee_task5_review.py::test_http_delete_run_is_user_scoped_and_maps_domain_errors tests/test_committee_task5_review.py::test_http_delete_run_maps_errors -q`

结果：`5 passed, 2 warnings in 0.88s`。

指定回归验证：

`PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/test_committee_repository.py tests/test_committee_task5_review.py -q`

结果：`46 passed, 2 warnings in 1.01s`。

## 修改文件

- `backend/app/advisor/committee/routes.py`
  - 新增认证 DELETE 路由。
  - 调用仓储软删除接口并传递用户范围及审计字段。
  - 映射 404/409 领域错误。
- `backend/tests/test_committee_task5_review.py`
  - DELETE 纳入认证覆盖。
  - 新增成功响应、用户范围、审计参数及错误映射测试。
- `docs/superpowers/reports/committee-delete-task-2-report.md`
  - 本实施报告。

未修改前端或底层仓储；工作区不是 Git 仓库，未执行 commit。

## 自检

- [x] 测试先行，并观察到预期 RED（405）。
- [x] 生产代码保持最小实现。
- [x] DELETE 路由由 `get_current_user` 保护。
- [x] `soft_delete_run` 的前两个位置参数为认证用户 ID 和 URL 中的 run ID。
- [x] `deleted_by` 为认证用户 ID。
- [x] `deleted_at` 为带时区时间。
- [x] 成功响应格式符合计划。
- [x] 404、409 映射符合计划。
- [x] 未调用 `_infra()`。
- [x] 指定的两个测试文件全部通过。
- [x] 修改文件 IDE lint 检查无错误。
- [x] 未修改前端或仓储。
- [x] 未执行 Git 提交。

## Concerns

- 测试输出包含两个既有依赖弃用警告：Starlette `TestClient` 的 httpx 使用方式，以及 Python `crypt` 模块弃用。它们不影响本任务测试通过，也不是本次修改引入。

## 审查修复（2026-07-23）

### Important 修复

严格审查指出原实现虽然没有直接调用 `_infra()`，但 DELETE 使用的
`_repository()` 会在 Redis 启用时调用 `create_queue()` 和
`reconcile_stale_runs()`。因此本报告早先关于 DELETE 不访问 Redis/RQ 的
结论不成立。

本次修复：

- 新增纯 Mongo 仓储构造 helper `_plain_repository()`，仅调用
  `CommitteeRepository.from_default_database()`。
- 现有 `_repository()` 复用 `_plain_repository()` 后，继续保留原来的
  stale-run reconcile 行为，其他路由语义不变。
- `delete_run()` 改为只调用 `_plain_repository()`，不经过
  `_repository()` 或 `_infra()`。
- 原有用户隔离和错误映射测试改为 patch `_plain_repository()`。
- 新增真实路由测试，不替换 `_repository()`；仅替换
  `CommitteeRepository.from_default_database` 返回 mock repository，并将
  `create_queue`、`reconcile_stale_runs`、`initialize_checkpoint_saver`、
  `_infra` 设置为调用即失败，从而直接验证 DELETE 的基础设施隔离边界。

### 修复 RED

新增隔离测试后、修改生产代码前运行：

`PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/test_committee_task5_review.py::test_http_delete_run_does_not_touch_task_infrastructure -q`

结果：`1 failed, 2 warnings`。堆栈确认 DELETE 经 `_repository()` 调用了
`create_queue(settings)`，由 `pytest.fail` 中止；这直接复现了审查指出的
根因。

### 修复 GREEN

聚焦测试：

`PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/test_committee_task5_review.py::test_http_delete_run_does_not_touch_task_infrastructure tests/test_committee_task5_review.py::test_http_delete_run_is_user_scoped_and_maps_domain_errors tests/test_committee_task5_review.py::test_http_delete_run_maps_errors -q`

结果：`5 passed, 2 warnings in 0.86s`。

指定完整回归：

`PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/test_committee_task5_review.py tests/test_committee_repository.py -q`

结果：`47 passed, 2 warnings in 1.04s`。

IDE lint 检查：`routes.py` 与 `test_committee_task5_review.py` 均无错误。

### 修复后自检

- [x] DELETE 仅通过 `_plain_repository()` 构造 Mongo repository。
- [x] DELETE 不调用 `create_queue`。
- [x] DELETE 不调用 `reconcile_stale_runs`。
- [x] DELETE 不调用 `initialize_checkpoint_saver`。
- [x] DELETE 不调用 `_infra`。
- [x] 原有 `_repository()` 仍保留 reconcile 行为。
- [x] 用户隔离、审计字段和 404/409 映射测试继续通过。
- [x] 未修改前端或底层仓储。
- [x] 未执行 Git 提交。

### 修复后 Concerns

- 仍有两个既有依赖弃用警告：Starlette `TestClient` 的 httpx 使用方式和
  Python `crypt` 模块弃用；与本次修复无关。
