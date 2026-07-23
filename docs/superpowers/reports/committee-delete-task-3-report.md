# 投委会软删除 Task 3 实施报告

## 结论

已按计划实现前端 API 客户端 `deleteCommitteeRun`：

- 函数签名：`deleteCommitteeRun(runId: string, signal?: AbortSignal): Promise<{ run_id: string; deleted: true }>`
- 请求：`DELETE /api/advisor/committee/runs/{encodeURIComponent(runId)}`
- 复用既有 `request()` helper，自动携带 Bearer 认证头
- 严格 TDD：先写测试确认 RED，再最小实现通过 GREEN

## RED

严格先修改测试，未预先修改生产代码。

1. 在 `committeeApi.test.ts` 导入 `deleteCommitteeRun` 并添加用例「删除会议使用 DELETE、认证头和编码后的 run id」。

2. 运行：

   ```bash
   cd frontend-advisor
   npm test -- --run src/committee/committeeApi.test.ts
   ```

   结果：

   ```
   Test Files  1 failed (1)
        Tests  1 failed | 16 passed (17)

   FAIL  src/committee/committeeApi.test.ts > committee API > 删除会议使用 DELETE、认证头和编码后的 run id
   TypeError: deleteCommitteeRun is not a function
   ```

   失败原因：`deleteCommitteeRun` 尚未导出，符合预期 RED。

## GREEN

在 `cancelCommitteeRun` 前添加最小 `deleteCommitteeRun` 实现：

```typescript
export function deleteCommitteeRun(runId: string, signal?: AbortSignal) {
  return request<{ run_id: string; deleted: true }>(
    `/runs/${encodeURIComponent(runId)}`,
    { method: 'DELETE', signal },
  )
}
```

运行：

```bash
cd frontend-advisor
npm test -- --run src/committee/committeeApi.test.ts
```

结果：

```
Test Files  1 passed (1)
     Tests  17 passed (17)
Duration  405ms
```

全部 PASS。

## 修改文件

- `frontend-advisor/src/committee/committeeApi.ts`
  - 在 `cancelCommitteeRun` 前新增 `deleteCommitteeRun` 导出函数。
- `frontend-advisor/src/committee/committeeApi.test.ts`
  - 导入 `deleteCommitteeRun`。
  - 在 `committee API` describe 中新增 DELETE 请求测试。
- `docs/superpowers/reports/committee-delete-task-3-report.md`
  - 本实施报告。

未修改页面组件或其他文件；工作区不是 Git 仓库，未执行 commit。

## 自检

- [x] 测试先行，并观察到预期 RED（`deleteCommitteeRun is not a function`）。
- [x] 生产代码保持最小实现（6 行函数，复用 `request`）。
- [x] URL 使用 `encodeURIComponent(runId)` 编码特殊字符（如 `run/1` → `run%2F1`）。
- [x] HTTP 方法为 `DELETE`。
- [x] 认证头通过 `request()` 自动注入 Bearer token。
- [x] 支持可选 `AbortSignal` 参数。
- [x] 返回类型 `{ run_id: string; deleted: true }` 符合计划。
- [x] `src/committee/committeeApi.test.ts` 全部 17 项通过。
- [x] 修改文件 IDE lint 检查无错误。
- [x] 未修改页面组件（Task 4 范围）。
- [x] 未执行 Git 提交。

## Concerns

- 当前测试仅覆盖成功路径（URL、method、Authorization）；未覆盖 404/409 错误映射。这与计划中 Task 3 范围一致，错误处理由既有 `request()` 统一抛出 `CommitteeApiError`，Task 4 工作台交互可再补充 UI 层测试。
- 未做端到端联调；后端 DELETE 路由已在 Task 2 完成，前端客户端与后端契约一致，联调留待 Task 4 或集成测试阶段。
