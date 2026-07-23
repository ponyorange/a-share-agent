# 投委会软删除 Task 4 审查

## 结论

**APPROVED**

本轮复审范围为 Task 4 工作台删除交互、上次审查的两个 Important must-fix、本次复审 diff 包，以及当前 `CommitteePage.tsx`、`CommitteePage.test.tsx`、`styles.css`。按要求未重跑完整测试；实现者报告的最新协调验证为 `CommitteePage.test.tsx` 与 `committeeApi.test.ts` 共 54 项通过。

## Spec compliance

### Critical

无。

### Important

无。

### Minor

无。

### 已符合

- 删除入口仅在 `completed`、`failed`、`cancelled` 终态详情中渲染；`running` 等活跃状态没有删除入口，因此 UI 不会对活跃会议发起 DELETE。
- 确认文案为“只会从历史列表隐藏此会议，不会撤销审批或订单。确认删除？”，符合只隐藏历史、不撤销审批或订单的约束；取消确认不会调用删除 API。
- 删除成功后只在前端清空当前详情视图、时间线、工件、审批弹窗和流状态，并刷新/过滤历史；没有清除事件、产物、checkpoint、审批、订单或模拟交易的客户端行为。
- 删除成功后会本地移除 `deletedId`，再使用刷新结果自动选择下一条；空列表时保持空态；删除失败时保留当前详情并显示错误。
- 上次 Important must-fix 1 已关闭：`deletedRunIds` tombstone 在 DELETE 成功解析后、aborted early return 前写入；`refreshRuns()` 对每个成功列表响应先过滤 tombstone，再把同一份 `visibleRuns` 用于 `setRuns` 和返回值。因此删除前在途的旧列表响应、删除所属刷新和后续会话内刷新都不能把已删除 ID 写回历史或返回给自动选择逻辑。
- 上次 Important must-fix 2 已关闭：新增 deferred 乱序回归测试先制造删除前在途旧列表请求，让删除所属刷新先选择 `run-2`，再让旧响应返回 `[deletedRun, nextRun, staleOtherRun]`；测试等待 `run-3` 出现在历史，正向证明旧响应已应用到列表写入路径，同时断言 `run-live` 没有复活且当前详情仍为 `run-2`。

## Code quality

### Critical

无。

### Important

无。

### Minor

无。

## 必须修改

无。
