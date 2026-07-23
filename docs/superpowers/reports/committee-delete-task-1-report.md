# 投委会会议记录软删除 Task 1 报告

## 状态

DONE

## 实施范围

按 `docs/superpowers/plans/2026-07-23-committee-run-soft-delete.md` 第 37-286 行实施，仅修改 Task 1 指定的领域模型、仓储、仓储测试，并新增本报告。未改动 HTTP API 或前端，未执行 Git 命令。

## RED

先在 `backend/tests/test_committee_repository.py` 添加：

- `_terminal_run` 终态构造器；
- 终态会议软删除后不可由普通读取访问、但事件和产物保留的测试；
- 活跃状态、跨用户、重复删除均被拒绝的测试；
- 普通会议读取包含 `deleted_at=None` 过滤条件的断言。

执行命令：

```bash
cd backend
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest \
  tests/test_committee_repository.py::test_soft_delete_terminal_run_hides_it_but_preserves_audit_rows \
  tests/test_committee_repository.py::test_soft_delete_rejects_active_foreign_and_already_deleted_runs -q
```

结果：符合预期地失败，`2 failed, 1 warning`，两个失败均为：

```text
AttributeError: 'CommitteeRepository' object has no attribute 'soft_delete_run'
```

这证明测试因缺少目标仓储能力而失败，而非测试拼写或环境错误。

## GREEN

最小实现内容：

- `CommitteeRun` 新增成对出现的 `deleted_at`、`deleted_by` 字段；
- 将 `deleted_at` 纳入既有 UTC 时间校验器；
- 增加仅终态可删除、删除时间不得早于完成时间的领域不变量；
- `CommitteeRepositoryProtocol` 增加指定的 `soft_delete_run` 签名；
- `get_run`、`list_runs` 增加 `deleted_at=None` 过滤；
- 实现终态检查、候选模型预校验和基于版本/状态/未删除条件的原子 CAS 软删除；
- CAS 失败时区分记录已不可见、状态并发改变和版本冲突；
- 不删除或修改事件、产物审计记录。

执行命令：

```bash
cd backend
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest \
  tests/test_committee_repository.py -q
```

结果：`11 passed, 1 warning in 0.91s`，退出码 0。

## 修改文件

- `backend/app/advisor/committee/models.py`
- `backend/app/advisor/committee/repository.py`
- `backend/tests/test_committee_repository.py`
- `docs/superpowers/reports/committee-delete-task-1-report.md`

## 自检

- [x] 严格先写测试并观察到预期 RED，再写生产代码。
- [x] `soft_delete_run` 签名与计划一致。
- [x] 仅 `COMPLETED`、`FAILED`、`CANCELLED` 可软删除。
- [x] 软删除记录对 `get_run` 和 `list_runs` 不可见。
- [x] 跨用户访问和重复删除返回 `RunNotFound`。
- [x] 软删除使用版本、状态和 `deleted_at=None` 作为原子更新条件。
- [x] 删除后版本递增，`updated_at` 使用删除时间。
- [x] 事件和产物审计记录保持不变。
- [x] 既有 Fake Collection 中的 `list_indexes()` baseline 修复仍保留。
- [x] 编辑文件的 IDE lint 检查无错误。
- [x] 未执行 commit；工作区不是 Git 仓库。
- [x] 未修改 Task 1 之外的产品代码、HTTP API 或前端。

## Concerns

无功能性 concerns。测试输出包含来自 `passlib` 对 Python `crypt` 模块的既有弃用警告；它与本任务改动无关，不影响测试通过。

## 审查修复（2026-07-23）

### 修复内容

- 为 `FakeCollection` 增加一次性、可控的 `before_find_one_and_update` 竞争注入点。
- 增加版本变化竞争测试：并发方先递增版本并更新时间，删除方必须抛出 `VersionConflict`，且不得覆盖并发版本、时间或写入删除标记。
- 增加状态变化竞争测试：并发方先将记录改为合法的活跃状态，删除方必须抛出 `IllegalStatusTransition`，且不得覆盖并发状态、时间或写入删除标记。
- 增加抢先删除竞争测试：并发方先写入删除人和删除时间，后到删除方必须抛出 `RunNotFound`，且不得覆盖胜出者。
- 增加直接 `CommitteeRun` 模型测试，覆盖：
  - 只提供 `deleted_at`；
  - 只提供 `deleted_by`；
  - 非终态记录带删除字段；
  - 删除时间早于完成时间；
  - naive `deleted_at`；
  - 非 UTC `deleted_at`。
- 将查询记录拆分为 `find_one_queries` 与 `find_queries`；断言同时要求 `"deleted_at" in query` 且值为 `None`，分别保护 `get_run` 和 `list_runs` 的查询形状。

### TDD/测试保护验证

本轮审查指出的是测试缺口，而不是已知生产行为缺陷。因此先只新增测试和 fake 竞争注入，不修改生产实现，然后执行新增测试：

```bash
cd backend
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest \
  tests/test_committee_repository.py -q \
  -k 'soft_delete_version_race or soft_delete_status_race or soft_delete_race_with_prior_delete or committee_run_requires_delete_fields_together or committee_run_rejects_deletion_in_non_terminal_status or committee_run_rejects_deletion_before_completion or committee_run_rejects_non_utc_deletion_time or soft_delete_terminal_run_hides_it_but_preserves_audit_rows'
```

结果：`10 passed, 10 deselected, 1 warning in 0.86s`，退出码 0。

新增竞争测试在 `get_run` 与 `find_one_and_update` 之间真实改变 fake collection 中的持久化文档，并断言并发写入原值；因此删除 CAS 缺少 `version`、`status` 或 `deleted_at=None` 任一对应条件时，各自场景将不再抛出预期异常或会覆盖胜出写入。模型测试直接调用 `CommitteeRun.model_validate`，不依赖仓储的重复校验。当前生产实现已满足这些保护，未扩大或修改生产代码。

### 完整 GREEN

执行命令：

```bash
cd backend
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest \
  tests/test_committee_repository.py -q
```

结果：`20 passed, 1 warning in 0.90s`，退出码 0。编辑文件的 IDE lint 检查无错误。

### 本轮修改文件

- `backend/tests/test_committee_repository.py`
- `docs/superpowers/reports/committee-delete-task-1-report.md`

未修改领域模型、仓储生产实现、HTTP API 或前端；未执行 Git 命令。

### 本轮 Concerns

无功能性 concerns。仍仅有 `passlib` 使用 Python `crypt` 模块产生的既有弃用警告，与本任务无关。
