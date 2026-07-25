# 数据子 Agent 实时进度展示设计

## 目标

当投研助手调用 `delegate_data_task` 后，用户不再面对最长约一分钟的空白等待。
前端应实时展示数据子 Agent 的安全工作进度，包括数据源、接口名、返回行数、
截断状态和稳定错误码，同时不泄露模型思维链、Python 源码、密钥、完整参数或原始数据。

## 现状与根因

投研助手的聊天接口已使用 SSE，事件顺序为：

`meta → tool* → token* → done | error`

主 Agent 通过 `agent.stream()` 产生事件，但 `delegate_data_task` 内部同步调用
`run_data_agent()`，而数据子 Agent 又使用同步 `agent.invoke()`。主 Agent 在工具执行期间
被阻塞，只能等整个委派完成并收到 `ToolMessage` 后才发送 `tool` 事件。

因此，现有前端在子 Agent 工作期间只能显示空助手气泡和“生成中”，无法看到内部阶段。

## 方案选择

采用“请求级进度通道 + SSE 多路复用”方案。

不采用仅显示固定心跳的方案，因为它无法满足展示数据源、接口和行数的需求。
不把子 Agent 改成独立后台任务，因为当前目标只要求改善一次聊天请求的等待体验，
后台任务会额外引入任务持久化、重连和取消语义，超出本次范围。

## 总体架构

### 请求级进度通道

新增独立进度模块，提供：

- `ProgressEvent`：受控的进度事件结构；
- `ProgressSink`：接收进度事件的回调协议；
- `ContextVar`：将当前请求的 sink 安全传递到主 Agent 工具和数据子 Agent；
- `emit_progress()`：无 sink 时为空操作，不影响非流式调用和测试；不接受调用方自由文本 `message`，仅接受白名单结构化字段。

进度事件只允许以下字段：

- `phase`：固定为 `data_agent`；
- `step`：允许的阶段枚举；
- `status`：`started | completed | failed`；
- `message`：模块内部根据 `step/status` 映射生成的固定中文文案，调用方不可传入自由文本；
- `source`：可选，数据源 ID；
- `interface`：可选，接口名；
- `rows`：可选，返回行数；
- `truncated`：可选，是否截断；
- `error_code`：可选，稳定错误码（小写字母开头，仅小写字母/数字/下划线，最长 64 字符；非法值拒绝）。

禁止进度事件携带：

- Provider Token、API Key、Authorization 等凭据；
- `params_json` 或完整参数；
- Provider 返回样例或完整数据；
- 模型提示词、思维链或模型自由文本；
- Python 源码、stderr、堆栈或宿主路径。

### SSE 阻塞穿透

仅增加回调不足以解决问题，因为当前 `iter_agent_chat_events()` 与主 Agent
运行在同一线程，工具阻塞时生成器无法继续 `yield`。

聊天流改为生产者/消费者结构：

1. 外层 SSE 生成器创建线程安全队列；
2. 在工作线程中运行原有主 Agent 流；
3. 工作线程把 `meta`、`tool`、`token`、`done`、`error` 放入队列；
4. 数据子 Agent 通过请求级 sink 把 `subagent_progress` 放入同一队列；
5. 外层生成器持续消费队列并立即向客户端 `yield`；
6. 工作线程结束后发送内部完成哨兵，外层生成器退出。

工作线程需使用 `contextvars.copy_context()` 启动，确保用户绑定和进度 sink
不会跨请求串线。队列必须有界；进度事件采用非阻塞写入，队列满时可丢弃重复进度，
但不得阻塞数据查询或丢失最终 `done/error`。

非流式 `run_agent_chat()` 继续消费同一事件迭代器，但忽略
`subagent_progress`，保持现有返回协议不变。

## 子 Agent 进度埋点

埋点位于确定性的工具边界，不读取模型思维过程。

| 阶段 | 埋点位置 | 展示内容 |
|---|---|---|
| 启动 | `delegate_data_task` | 正在启动数据子 Agent |
| 列数据源 | `list_data_sources` | 正在检查可用数据源 |
| 搜索接口 | `search_data_interfaces` | 正在搜索 `{source}` 数据接口 |
| 读取定义 | `get_data_interface` | 正在读取 `{source}/{interface}` 参数定义 |
| 拉取开始 | `fetch_provider_data` 调用前 | 正在调用 `{source}/{interface}` |
| 拉取完成 | 工作区创建 dataset 后 | 已获取 N 行，显示截断状态 |
| 沙箱开始 | `run_python_analysis` 调用前 | 正在计算和整理数据 |
| 沙箱完成 | 结果证据写入后 | 计算完成 |
| 提交结果 | `submit_data_result` | 正在校验来源与结果 |
| 完成/失败 | `delegate_data_task` 返回前 | 数据查询完成或稳定错误码 |

同一 `step/status/source/interface` 的连续重复事件在后端去重，避免模型重试时刷屏。

## SSE 协议

新增事件：

```text
event: subagent_progress
data: {
  "phase": "data_agent",
  "step": "fetch",
  "status": "completed",
  "message": "已获取 53 行数据",
  "source": "akshare",
  "interface": "stock_zh_index_daily_tx",
  "rows": 53,
  "truncated": false
}
```

现有 `meta/tool/token/done/error` 结构保持不变。未知事件仍由旧前端忽略，
因此协议向后兼容。

## 前端交互

聊天页新增 `liveSubagentProgress` 状态。

等待期间在助手气泡下展示“数据子 Agent”进度面板：

- 顶部显示当前阶段和运行状态；
- 下方按时间顺序显示工具详情；
- 数据源、接口名、行数、截断和错误码使用短文本展示；
- 相同阶段更新原有条目，不无限追加；
- 收到首个回答 token 后将面板折叠，避免干扰最终回答；
- 收到 `done/error` 后停止动画。

完成后，将本次脱敏进度转换为现有 `tool_trace` 条目并随助手消息持久化，例如：

```json
{
  "tool": "data_agent.fetch_provider_data",
  "content": "akshare/stock_zh_index_daily_tx：53 行，未截断"
}
```

这样历史消息仍使用现有“工具调用”折叠区，不需要修改 Mongo 文档结构。
原始 `delegate_data_task` 返回 JSON 仍按现有限制截断，进度条目不包含原始数据。

## 错误与取消

- Provider 失败：发送 `failed`，只附稳定错误码；子 Agent 如能换源可继续展示后续阶段；
- 沙箱失败：发送稳定沙箱错误码，不展示 Python 或异常正文；
- 子 Agent 步数耗尽：显示“数据查询步骤达到上限”；
- SSE 客户端断开：取消请求级 sink，停止向队列写入；本次同步 Agent 是否继续执行
  保持现有行为，不在本次引入强制任务取消；
- 工作线程异常：转成现有 `error` 事件；
- 进度渲染异常：不得影响 token 和 `done` 消费。

## 预计改动

后端：

- 新增 `backend/app/advisor/agent/progress.py`
- 修改 `backend/app/advisor/agent/graph.py`
- 修改 `backend/app/advisor/agent/data_agent/delegate.py`
- 修改 `backend/app/advisor/agent/data_agent/provider_tools.py`
- 修改 `backend/app/advisor/agent/data_agent/sandbox.py`
- 修改 `backend/app/advisor/agent/data_agent/graph.py`

前端：

- 修改 `frontend-advisor/src/agentApi.ts`
- 修改 `frontend-advisor/src/pages/AgentChatPage.tsx`
- 按需修改 `frontend-advisor/src/styles.css`

不修改 Provider 协议、沙箱 HTTP 协议和 Mongo 消息 Schema。

## 测试

### 后端

- `emit_progress()` 在无 sink 时为空操作；
- ContextVar 在并发请求间隔离；
- 数据源搜索、接口读取、fetch、沙箱和 submit 发出正确的脱敏事件；
- 事件不含参数、数据样例、Python、密钥或异常堆栈；
- `delegate_data_task` 阻塞时，外层事件流可先收到多个 `subagent_progress`；
- `done/error` 不会因进度队列满而丢失；
- 非流式 `run_agent_chat()` 行为不变；
- 进度轨迹按限制持久化到现有 `tool_trace`。

### 前端

- 正确解析 `subagent_progress`；
- 流式等待时展示数据源、接口、行数和截断状态；
- 重复阶段更新而不是无限追加；
- token 到达后进度面板折叠；
- `done/error/AbortError` 后清理运行状态；
- 历史消息仍可在现有工具调用折叠区查看脱敏轨迹。

### 回归

使用以下提示词进行端到端验证：

> 用数据源查一下沪深300最近20个交易日的收盘价，并告诉我最新收盘、区间最高/最低，以及来源接口名。不要凭记忆编造。

验收标准：

1. 发起请求后数秒内出现“正在启动数据子 Agent”；
2. 查询期间至少显示搜索接口、拉取数据和沙箱计算阶段；
3. 拉取完成后展示数据源、接口名、行数和截断状态；
4. 最终回答正常流式输出；
5. 展示内容不含敏感参数、原始大表、Python 源码或思维链。
