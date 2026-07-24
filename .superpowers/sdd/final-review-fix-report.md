# Final Review Fix Report

## 状态

已一次修复最终评审提出的 3 个 Important 与 1 个相关 Minor，并补充回归测试。

## 修复

- 在请求级 `DatasetWorkspace` 中计数 Python 分析调用，最多允许 2 次；第 3 次固定返回 `python_retry_limit_exceeded`，且不会导出数据集或调用 `client.execute`。成功沙箱结果同时记录为本请求证据。
- `DatasetMeta.sample` 最多返回 5 行、每行最多 16 个字段且不超过 4096 bytes；字符串、嵌套深度与集合项数均受限，并带 `sample_trust="untrusted_provider_data"` 与 `sample_truncated`。Provider 任意对象及非有限数替换为固定 `[unsupported]`，不调用其 `__str__`/`__repr__`。
- `DatasetMeta` 不再保存或返回原始 `params`，仅保存递归脱敏、有深度/项数/字符串/字节上限的 `params_summary`；同时记录可校验的 `data_time`。
- `parse_data_agent_result` 接收请求 workspace 证据。最终 `sources` 的 `source`、`interface`、`params_summary`、`data_time`（以及模型提供时的 `rows`、`truncated`）必须匹配本请求真实数据集，否则固定返回 `invalid_agent_result`。
- 没有成功数据集且没有合法沙箱结果时，模型返回非空目标数据会转换为 `incomplete_agent_result`；真实部分数据可与 warnings/failures 一并返回。
- `run_data_agent` 已将当前 workspace 注入最终结果解析，确保校验作用于真实请求执行路径。

## TDD 证据

- RED：新增 6 个核心回归场景后运行，结果为 `6 failed, 1 warning`。失败分别证明第三次 Python 调用未受限、样例未设边界/不可信标记、原始 params 泄露、解析器未接受 workspace 证据。
- GREEN：相同 6 个核心场景修复后为 `6 passed, 1 warning`。
- 扩展覆盖：
  - 第 3 次 Python 分析拒绝且真实 `client.execute` 仅调用 2 次。
  - 20 行超多样例、100 字段大行、20KB 字段、Evil `__str__`、NaN。
  - Provider 工具返回 `params_summary` 而非 `params`。
  - source/interface/params_summary/data_time 任一伪造均拒绝。
  - 无证据成功数据转 incomplete。
  - 直接 Provider 数据即使已创建 dataset，未声明合法 source 仍转 incomplete。
  - 部分真实数据与 failures/warnings 可共存。

## 最终验证

- Task 1/2/5/6 数据 Agent 测试：
  - `90 passed, 1 warning in 1.00s`
- Ruff：
  - `All checks passed!`
- Compileall：
  - 退出码 0
- IDE lint：
  - 无错误

## Concerns

- 唯一警告是既有依赖 `passlib` 导入 Python `crypt` 的弃用警告，与本次改动无关。
- 未调用真实 LLM、Provider 或 Sandbox Controller；验证范围为隔离单元测试。

## 2026-07-25 全分支复审 Important 修复

### 状态

- `DatasetWorkspace` 现在为每次成功沙箱执行保存规范化 JSON、唯一 `result_id` 与有限摘要；证据条数受 Python 重试上限约束，单条大小受 `max_output_bytes` 约束。
- `run_python_analysis` 成功响应包含 `result_id`、`result_summary` 与规范化 `result`。
- 最终非空 `data` 必须规范化后精确匹配本请求某个成功沙箱结果，或使用仅含 `result_id` 与一致 `payload` 的显式引用；合法 source、任意其他沙箱成功均不能为伪造 data 背书。
- 无成功沙箱结果时，非空 `data` 固定转换为 `invalid_agent_result`；空 data 若无明确 failure 则转换为 `incomplete_agent_result`。
- 真实沙箱结果仍可与部分 `failures`、`warnings` 一并返回。

### TDD 与验证

- RED：新增核心场景后为 `8 failed, 58 passed, 1 warning`。
- GREEN：聚焦 workspace/sandbox/graph 为 `66 passed, 1 warning`。
- 完整数据 Agent 测试：`94 passed, 1 warning in 1.24s`。
- Ruff：`All checks passed!`。
- Compileall：退出码 0。
- IDE lint：无错误。

### Concerns

- 唯一警告仍为既有 `passlib`/`crypt` 弃用警告。
- 未调用真实 LLM、Provider 或 Sandbox Controller；验证范围为隔离单元测试。
