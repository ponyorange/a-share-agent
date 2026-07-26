# Agent 直接执行 Python 脚本设计

## 目标

为主 Agent（投研助手）提供写并执行 Python 脚本的能力，同时保留数据子 Agent
对 Provider 大表 / 跨源计算的专用链路。

## 已确认决策

- 方案：主 Agent 新工具 + 沙箱契约扩展；数据子 Agent 继续负责大表/跨源。
- 结果约定：优先使用脚本中的 `result`；若未赋值，则回传 stdout/stderr。
- 数据输入：支持无表脚本、小 JSON inline datasets、以及本轮工具产物登记为临时 dataset。
- 会话范围：仅当前请求生命周期；不做跨消息会话缓存。

## 架构

```text
主 Agent
  ├─ register_tool_dataset(name, tool_result_json)
  │     └─ 登记本轮临时 dataset（内存，请求级）
  ├─ run_python_script(code, dataset_ids_json?, inline_datasets_json?)
  │     └─ SandboxClient -> sandbox-controller -> runner
  └─ delegate_data_task(...)
        └─ 数据子 Agent -> run_python_analysis (require_result=true)
```

复用现有 Docker 沙箱（controller + runner）。不新增网络权限、不引入 pip、不持久化脚本文件。

## 主 Agent 工具

### `register_tool_dataset`

- 入参：`name`（dataset id）、`tool_result_json`（本轮某次成功工具返回的 JSON 文本）
- 行为：解析 JSON，校验行数/字节上限后写入请求级 workspace
- 失败：非法 JSON、超限、数量超限时直接返回结构化错误，不进沙箱

### `run_python_script`

- 入参：
  - `code`：Python 源码
  - `dataset_ids_json`：可选，引用本轮已登记 dataset id 列表
  - `inline_datasets_json`：可选，一次性小表 `{id: [row, ...]}`
- 行为：合并 inline + 已登记 datasets，调用沙箱，`require_result=false`
- 返回：成功时包含 `result`（可为 null）以及 `stdout`/`stderr`（若有）；失败时结构化 error

## Runner / Controller 契约

请求增加布尔字段 `require_result`（默认 `true`，兼容现有 data_agent）：

| `require_result` | 无 `result` 时 |
|------------------|---------------|
| `true` | 保持现状：`result_not_assigned` |
| `false` | 返回 `{ "result": null, "stdout": "...", "stderr": "..." }` |

有 `result` 时，两条路径都按现有 `json_safe` / 有限数值 / 输出字节上限序列化。

安全边界不变：

- 无网、非 root、`cap_drop ALL`、`no-new-privileges`
- import 白名单：`pandas` / `numpy` / `math` / `statistics` / `datetime`
- 超时 ≤ 30s，内存 ≤ 512MB，输出 ≤ 1MB
- 不注入宿主 env / token

若改动小且测试覆盖，controller 创建容器时补齐 `read_only=True`，与既有沙箱设计对齐。

## 限额（`config.yaml` → `agent_python`）

| 项 | 值 |
|----|----|
| 单次 inline / 登记 dataset | ≤ 200 行、≤ 200KB |
| 本轮登记 dataset 总数 | ≤ 5 |
| 沙箱超时 / 内存 / 输出 | 30s / 512MB / 1MB |
| 主 Agent 本轮 Python 调用次数 | ≤ 3 |

数据子 Agent 的 `data_agent` 限额与证据链保持不变。

## Prompt 规则

在主 Agent system prompt 增加：

1. 小计算、试跑、对本轮小结果二次加工 → `run_python_script`
2. Provider 外部数据、跨表/跨源、大表分析 → 仍 `delegate_data_task`
3. 需要把本轮工具结果喂给脚本时，先 `register_tool_dataset`
4. 禁止把未经验证/未工具返回的数据编造进沙箱
5. 解读优先级：`result` > stdout/stderr；向用户如实说明失败与截断

规则 15（委派数据子 Agent）保留，并明确与 `run_python_script` 的分工。

## 进度与前端

- 主 Agent 直连沙箱时复用现有 `sandbox` progress step
- 前端继续展示工具轨迹摘要；完整代码默认折叠（沿用现有 trace 能力，不强制新 UI）
- 不新增独立 step 类型（本迭代）

## 错误处理

| 情况 | 行为 |
|------|------|
| `SANDBOX_URL` / `SANDBOX_TOKEN` 缺失或非法 | 工具返回配置错误，不崩溃 |
| inline/登记超限、调用次数超限 | 工具层拒绝 |
| 超时 / 不可用 / import 拒绝 / 输出过大 | 结构化 error，主 Agent 转述 |
| 数据子 Agent 无 `result` | 仍失败（不因本功能放宽） |

## 测试重点

1. Runner：`require_result=false` 时 stdout 回退；`true` 时仍强制 `result`
2. 主 Agent 工具：无表、inline 小表、本轮登记 dataset、超限拒绝、调用次数上限
3. 委派链路不回归：`delegate_data_task` + `run_python_analysis` 证据规则
4. 配置缺失时的降级错误

## 验收标准

1. 主 Agent 可执行无表 `print` 脚本并得到 stdout 回传
2. 主 Agent 赋值 `result` 时可得到结构化结果
3. inline 小 JSON 与本轮 `register_tool_dataset` 可被 `datasets['id']` 使用
4. 超限、超时、缺沙箱配置时有明确错误
5. Provider 大表场景仍走数据子 Agent，且强制 `result` 不回归
6. 相关后端与沙箱测试通过

## 非目标

- 跨消息会话 dataset 缓存
- 多文件脚本、pip、联网
- 用户侧 Python 代码编辑器
- 放宽数据子 Agent 的 `result` 证据链
