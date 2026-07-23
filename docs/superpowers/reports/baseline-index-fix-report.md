# Baseline Index Fix Report

## 背景

运行基线测试时，`test_global_index_initialization_includes_committee_without_redis` 失败。该测试验证 `app.db.ensure_indexes()` 在全局索引初始化时会调用 `CommitteeRepository.ensure_indexes()`，且不会触发 Redis 客户端创建。

## 根因

1. **失败现象**：`AttributeError: 'Collection' object has no attribute 'list_indexes'`
2. **调用链**：`db_module.ensure_indexes()` → `_ensure_partial_unique_index()` → `collection.list_indexes()`
3. **触发位置**：`app/db.py` 第 69 行，对 `paper_trades` 集合创建 partial unique index 时
4. **根本原因**：生产代码 `_ensure_partial_unique_index` 使用 `list_indexes()` 检查现有索引是否与期望一致（避免重复创建或需要 `drop_index` 替换）。测试内嵌的 Fake `Collection` 只实现了 `create_index`，未实现 `list_indexes`，与当前生产逻辑不匹配。

### 修测试桩 vs 修生产代码

| 选项 | 判断 |
|------|------|
| 修生产代码 | 否。`list_indexes()` 是 PyMongo 标准 API，用于幂等索引管理，逻辑正确 |
| 修测试桩 | 是。测试目标是验证 Committee 索引路径被调用且不初始化 Redis，Fake Collection 应满足 `ensure_indexes()` 对集合的最小接口 |

## 修改文件

- `backend/tests/test_committee_repository.py`  
  在 `test_global_index_initialization_includes_committee_without_redis` 的 Fake `Collection` 中新增：

  ```python
  def list_indexes(self):
      return []
  ```

  返回空列表表示无既有索引，`_ensure_partial_unique_index` 会直接 `create_index`，无需 `drop_index`。

## 未修改范围

- 未改动 `app/db.py` 或 `_ensure_partial_unique_index` 逻辑
- 未改动投委会删除相关功能
- 未扩大测试或生产代码范围

## 测试命令

```bash
cd backend && PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/test_committee_repository.py tests/test_committee_task5_review.py -q
```

## 完整结果摘要

```
...............................                                          [100%]
=============================== warnings summary ===============================
.venv/lib/python3.12/site-packages/passlib/utils/__init__.py:854
  DeprecationWarning: 'crypt' is deprecated and slated for removal in Python 3.13

.venv/lib/python3.12/site-packages/fastapi/testclient.py:1
  StarletteDeprecationWarning: Using `httpx` with `starlette.testclient` is deprecated; install `httpx2` instead.

31 passed, 2 warnings in 1.12s
```

- **修复前**：1 failed, 22 passed（仅跑指定用例时）；完整两文件为 30 passed + 1 failed
- **修复后**：31 passed, 0 failed
- **警告**：2 条第三方库 DeprecationWarning，与本次修复无关

## 自检

- [x] 复现原始 RED 失败（`list_indexes` AttributeError）
- [x] 按 systematic debugging 定位到测试桩与生产 API 不匹配
- [x] 采用最小 diff：仅补 Fake `list_indexes` 返回 `[]`
- [x] 重跑指定两个测试文件，全部通过
- [x] 未修改投委会删除功能或生产索引逻辑
- [x] 工作区非 Git 仓库，未执行 commit
