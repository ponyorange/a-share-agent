# Baseline Index Fix — Code Review

**审查范围**：修复 `test_global_index_initialization_includes_committee_without_redis` 基线失败  
**审查日期**：2026-07-23  
**工作区**：`/Users/orange/Desktop/code/share-data`  
**依据材料**：
- `docs/superpowers/reports/baseline-index-fix-report.md`
- `backend/tests/test_committee_repository.py`（`test_global_index_initialization_includes_committee_without_redis`）
- `backend/app/db.py`（`_ensure_partial_unique_index`、`ensure_indexes`）

---

## 审查结论

**APPROVED**

---

## 1. Spec Compliance（任务符合性）

| 要求 | 结论 | 说明 |
|------|------|------|
| 修复既有基线失败 | ✅ 通过 | 根因与报告一致：`Fake Collection` 缺 `list_indexes`，导致 `_ensure_partial_unique_index` 在 `paper_trades` 处抛 `AttributeError`，测试在到达 Committee 断言前即失败 |
| 根因定位正确 | ✅ 通过 | 调用链 `ensure_indexes()` → `_ensure_partial_unique_index()` → `collection.list_indexes()` 与 `db.py:38` 一致；PyMongo 标准 API，生产逻辑合理 |
| 不得改生产代码 | ✅ 通过 | `backend/app/db.py` 未改动；`_ensure_partial_unique_index` 仍为基于 `list_indexes` 的幂等索引管理 |
| 不得改投委会删除功能 | ✅ 通过 | 变更仅限单测内嵌 Fake `Collection`；未发现 committee 删除相关生产或测试改动 |
| 最小范围修复 | ✅ 通过 | 仅新增 3 行 `list_indexes` 桩，返回 `[]` |
| 测试目标不变 | ✅ 通过 | 仍验证 `CommitteeRepository.ensure_indexes()` 被调用一次，且 `redis_client.create_client` 不被触发 |

**任务边界判断**：修测试桩而非生产代码，与任务约束及报告中的选项分析一致，属于正确决策。

---

## 2. Code Quality（代码质量）

### 2.1 测试桩补法是否正确

**结论：正确。**

```python
def list_indexes(self):
    return []
```

- `_ensure_partial_unique_index` 将 `list_indexes()` 结果按 `idx["name"]` 建字典，再查目标 `name`；空列表表示「无同名既有索引」，走 `create_index` 分支，与「空库首次建索引」语义一致。
- 生产环境中即便存在默认 `_id_` 索引，也不会与 `user_id_1_external_idempotency_key_1` 等 partial index 名称冲突，故 `[]` 与「仅含 `_id_`」在此函数逻辑上等价。
- 桩仅需可迭代；不必模拟 PyMongo `CommandCursor` 或 context manager，对本测试足够。
- Fake `Collection` 已有 `create_index`；在 `list_indexes` 返回 `[]` 时不会触发 `drop_index`，故无需额外实现 `drop_index`。

### 2.2 是否掩盖生产问题

**结论：未掩盖与本测试职责相关的生产问题。**

| 维度 | 评估 |
|------|------|
| 测试意图 | 本用例验证「全局 `ensure_indexes()` 会调用 Committee 索引路径且不初始化 Redis」，而非 partial unique index 的对账/替换逻辑 |
| 桩的诚实性 | `[]` 不伪造「索引已存在且一致」从而跳过 `create_index`；partial index 路径仍执行 `create_index`，与无历史索引场景一致 |
| 未覆盖路径 | `_ensure_partial_unique_index` 在「索引存在但 keys/unique/partialFilter 不一致」时需 `drop_index` 再建——本测试不覆盖，属**既有**测试分层缺口，非本次引入的掩盖 |
| 修复后执行深度 | 修复前测试在 `paper_trades` 处提前崩溃；修复后 `ensure_indexes()` 完整跑通（含 migrate 的 try/except）。这是**预期行为**：测试名即「全局索引初始化」，完整调用链更贴近真实启动，且 migrate 失败会被现有 try/except 吞掉并走兜底 `create_index`，Fake 桩仍满足 |

### 2.3 与 `_ensure_partial_unique_index` 生产逻辑的对照

```30:53:backend/app/db.py
def _ensure_partial_unique_index(
    collection: Any,
    keys: list[tuple[str, int]],
    *,
    name: str,
    partial_filter: dict[str, Any],
) -> None:
    """Create/replace a unique index that ignores missing/null key values."""
    existing = {idx["name"]: idx for idx in collection.list_indexes()}
    current = existing.get(name)
    # ... same_keys / same_unique / same_partial → return or drop_index ...
    collection.create_index(...)
```

桩补 `list_indexes` 是使 Fake 与当前生产契约对齐的**必要最小接口**，不是绕过生产校验。

---

## 3. Findings

### Critical

*无*

### Important

*无*

### Minor

1. **M1 — partial index 对账路径无单测**  
   `_ensure_partial_unique_index` 的「已存在且一致则跳过」「不一致则 drop 再建」分支仍无专门测试。与本次修复无关，但若未来再改索引逻辑，建议在 `db` 或 integration 层补覆盖，而非在本 Committee/Redis 用例中扩大 scope。

2. **M2 — 桩文档可选增强**  
   可在 `list_indexes` 旁加一行注释说明「空列表表示无同名索引，使 partial index 走 create 分支」，便于后续维护者理解非 PyMongo 完整模拟的原因（非阻塞项）。

3. **M3 — 工作区非 Git 仓库**  
   报告已说明无法 diff/commit；审查通过文件内容核对，变更范围与报告描述一致。

---

## 4. 审查清单

- [x] 阅读 baseline 报告与相关实现
- [x] 核对测试桩与 `_ensure_partial_unique_index` 契约
- [x] 确认未改生产代码与 committee 删除功能
- [x] 评估是否掩盖生产问题
- [x] 未重跑报告中已执行的 pytest（依审查指令）

---

## 5. 最终裁决

**APPROVED** — 变更恰好满足任务：以最小 diff 修复测试桩与生产 API 的不匹配，不扩大 scope，不掩盖本测试所验证的行为。
