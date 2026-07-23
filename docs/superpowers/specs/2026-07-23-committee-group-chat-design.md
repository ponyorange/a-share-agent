# 投委会群聊界面设计

## 目标

把现有投委会工作台的主区域改造成只读群聊，让用户实时看到各角色发言，同时保留现有结构化报告、证据、回测、风控和主席决策。

第一版不允许用户发言或干预会议。群聊是投委会执行过程的可视化与审计入口，不改变现有图的决策规则。

## 现状约束（实施前基线）

当前页面是 dashboard 分区，不是聊天流：`events` 负责节点进度，`artifacts` 负责角色正文。SSE 的 `completed` / `degraded` 等事件通常不含 thesis、argument、rationale；正文要等 `node_completed` 后前端 debounce 拉取详情才能展示。

因此第一版不能只靠现有 SSE 拼聊天气泡。必须新增 `message_*` 流式事件，并继续用 Mongo artifacts / `message_completed` 做权威回放。旧会议仍从前端把 artifacts 转成完整消息，不写回数据库。

额外注意：

- 同一节点常有 `completed` + `node_completed` 两条事件，气泡不得对每条事件各渲染一次。
- 四位分析师并行完成，event `sequence` 反映完成先后，不等于业务展示序；聊天最终顺序以完成消息与逻辑角色序为准。
- live SSE 的 `data` 目前未必带顶层 `node`；新消息协议必须自带 `role` / `node`，不能依赖旧字段。
- `errors`、`model_calls`、原始 snapshot 默认不进聊天气泡。

## 已确认的产品决策

- 群聊作为主界面，结构化详情放在右侧抽屉。
- 角色消息真实逐 token 展示。
- 数据冻结、回测、风控等非 LLM 节点作为独立系统角色发言。
- 消息以自然语言为主，可展开证据、回测和风控数据卡片。
- 采用单次流式 JSON；供应商或解析不支持时自动降级为完整消息逐字展示。
- 旧会议从已有 artifacts 和 events 转换为完整聊天记录，不迁移历史数据。

## 界面设计

### 整体布局

- 左侧保留历史会议列表、筛选和当前选择。
- 主区域顶部固定会议状态、参与者、耗时、Token 和流状态。
- 主区域主体为按时间排列的消息流。
- 主区域右上角提供“查看完整报告”，打开右侧详情抽屉。
- 详情抽屉复用现有时间线、四方报告、辩论、回测、风控、主席决策和证据视图。

### 参与角色

| 节点 | 群聊身份 |
|---|---|
| `prepare` | 数据助手 |
| `fundamental` | 基本面分析师 |
| `technical` | 技术分析师 |
| `news` | 新闻分析师 |
| `quant` | 量化分析师 |
| `bull` | 多方辩手 |
| `bear` | 空方辩手 |
| `trader` | 交易员 |
| `backtest` | 回测员 |
| `risk` | 风控官 |
| `chair` | 主席 |

每个角色具有固定头像、颜色和名称。四位分析师并行运行时可以同时显示“正在输入”。主席最终消息使用强调样式。

### 消息样式

每条消息包含：

- 角色头像和名称；
- 消息时间；
- 流式、完成、降级或失败状态；
- 对外结论和简要依据；
- 可选的可展开数据卡；
- 可选的证据引用入口。

群聊只展示角色对外输出，不展示完整 prompt、原始模型 JSON、隐藏推理、API Key、异常堆栈或其他内部实现信息。

## 消息模型

新增统一的聊天消息协议：

```text
message_id
run_id
attempt
role
node
round
content
status
sequence
created_at
completed_at
card_kind
card_ref
```

- `message_id` 由 `run_id + attempt + node + round` 确定性生成。
- `status` 为 `streaming`、`completed`、`degraded`、`failed`。
- `card_kind` 标识证据、交易提案、回测、风控或最终决策。
- `card_ref` 指向现有 artifact，不重复持久化大对象。

## LLM 流式协议

### 角色响应

每个 LLM 角色的结构化响应新增首字段 `chat_message`。Prompt 要求该字段是适合直接展示给用户的简洁发言，不能包含内部推理。

完整响应仍由现有 Pydantic 模型校验，`analyst_reports`、`debate_turns`、`trade_proposals` 和最终决策的结构化语义保持不变。

### 增量解析

`ChatModelRoleRunner` 改用模型流式接口。增量解析器只提取 JSON 中 `chat_message` 字符串的已解码内容，正确处理：

- 中文与 Unicode 转义；
- 引号和反斜杠；
- JSON 字符串跨 chunk；
- 一个 chunk 中包含多个字符；
- 最后一段与完整 JSON 同时到达。

后端发布三类事件：

```text
message_started  {message_id, role, node, round}
message_delta    {message_id, offset, delta}
message_completed {message_id, role, node, round, content, status, card_kind, card_ref}
```

`message_started` 和 `message_delta` 只写 Redis Stream。`message_completed` 使用确定性事件键写入 Mongo，再通过现有 outbox/SSE 路径发布。

### 降级

出现以下情况时停止发布增量，但不破坏角色调用：

- 模型供应商不支持流式；
- 增量 JSON 无法安全解析；
- 流式连接中断，但完整调用可以重试；
- 未在合理位置收到 `chat_message`。

角色完整响应校验成功后，后端仍发布一条 `message_completed`。前端若没有收到增量，则对完整内容执行本地逐字展示。

## 非 LLM 节点消息

非 LLM 节点由后端根据确定性结果生成消息，不额外调用模型：

- 数据助手：快照冻结成功、降级来源或失败原因；
- 回测员：是否通过、样本数、交易数、收益、回撤和拒绝原因；
- 风控官：通过、修订或否决，以及命中的硬规则；
- 主席：最终动作、目标权重、置信度和不可推翻的风控结论。

消息引用现有 artifact，卡片按需展开。

## 前端状态与渲染

新增独立聊天 reducer，按 `message_id` 管理消息：

- `message_started` 创建临时消息；
- `message_delta` 仅在 `offset` 等于当前内容长度时追加；
- 重复 offset 忽略；
- offset 出现缺口时标记等待完整消息，不拼接不确定内容；
- `message_completed` 覆盖临时内容并设为完成；
- Mongo 历史消息优先于 Redis 临时消息。

为降低渲染频率，token delta 在浏览器中按短时间窗口批量刷新。用户位于消息底部时自动滚动；用户向上查看历史时停止自动滚动并显示“回到最新”。

## 历史回放

新会议从 Mongo `message_completed` 事件恢复完整消息。

旧会议没有聊天事件时，前端适配层从现有数据生成消息：

- `analyst_reports` → 四位分析师；
- `debate_turns` → 多方/空方；
- `trade_proposal(s)` → 交易员；
- `backtest_verdict` → 回测员；
- `risk_verdict` → 风控官；
- `final_decision` → 主席；
- 快照和错误 artifacts → 数据助手。

转换结果只存在于前端，不回写 Mongo。

## 顺序与并发

- Mongo 事件使用现有单调 sequence 作为权威顺序。
- 流式临时消息按首次 `message_started` 到达顺序显示。
- 同一角色同一轮次只允许一个活动 `message_id`。
- 四位分析师可以并行发言；同一消息内部严格按 offset 排序。
- 完成事件到达后，最终权威顺序覆盖临时顺序。

## 错误处理

- 角色失败时显示系统失败消息，不伪造角色结论。
- 节点降级时消息带“降级”标识和可查看原因。
- SSE 断线后沿用 `Last-Event-ID` 重连。
- Redis 中增量被裁剪时，Mongo 完整消息仍可恢复。
- 会议中止时保留已完成消息，未完成消息标记为中断。
- 完成消息写 Mongo 使用确定性事件键，重试不得生成重复消息。

## 测试

### 后端

- 增量 JSON 解析：中文、Unicode、转义、跨 chunk 和非法输入；
- `message_started/delta/completed` 顺序与 offset；
- 并行角色的消息隔离；
- 完成消息幂等持久化；
- Redis 断线和非流式供应商降级；
- 非 LLM 节点消息与 artifact 一致；
- 不泄露 prompt、密钥、堆栈和隐藏推理。

### 前端

- reducer 的拼接、去重、缺口和完成覆盖；
- SSE 重放与 Mongo hydration；
- 并发角色输入状态；
- 旧会议 artifacts 转换；
- 数据卡和详情抽屉；
- 自动滚动与“回到最新”；
- 终态、失败和中止消息。

### 端到端验收

1. 发起会议后，分析角色开始时显示输入状态。
2. 角色发言随真实模型 token 到达逐字出现。
3. 刷新页面后，所有已完成消息完整恢复且不重复。
4. 回测、风控和主席消息带对应数据卡。
5. 主席消息与结构化最终决策一致。
6. 断开并恢复 SSE 后，消息不缺字、不重复、不乱序。

## 不在第一版范围

- 用户发言、追问或中途干预；
- 私聊、@角色、角色手动选择；
- 对历史消息编辑或删除；
- 展示模型隐藏推理；
- 持久化每一个 token delta；
- 修改投委会决策图、风控规则或审批语义。

