# Task 3 前端删除 API 严格审查

## 最终结论

**APPROVED**

Task 3 实现符合计划第 433–510 行的接口与测试要求。生产代码正确编码 `runId`、发送 `DELETE`、透传可选 `AbortSignal`、复用既有 `request()` 认证与错误处理，并保留精确的返回类型。现有测试对计划明确要求的 URL、method 和认证头进行了真实的请求边界验证。

本次按要求直接审查文件，未重跑实施报告中的测试。

## Spec compliance

**结论：PASS**

- `frontend-advisor/src/committee/committeeApi.ts:166-171` 导出了计划要求的 `deleteCommitteeRun(runId: string, signal?: AbortSignal)`。
- URL 使用 `` `/runs/${encodeURIComponent(runId)}` ``；`run/1` 会成为 `run%2F1`，不会被误解释为额外路径段。
- `RequestInit.method` 明确为 `DELETE`。
- 可选 `signal` 原样传入 `request()`，而 `request()` 又通过 `{ ...init, headers }` 将其传给 `fetch`，不存在丢失。
- 通过已有 `request()` 复用 `getToken()` 和 Bearer 认证注入，没有复制认证逻辑。
- 泛型实参 `{ run_id: string; deleted: true }` 使推断返回类型为 `Promise<{ run_id: string; deleted: true }>`；`deleted` 不是宽化后的 `boolean`。
- `frontend-advisor/src/committee/committeeApi.test.ts:38-52` 与计划 Step 1 的要求一致，验证了完整 URL、DELETE method 和 Authorization。

## Code quality

**结论：PASS（有非阻塞测试改进项）**

- 实现保持最小化，并与相邻的 `getCommitteeRun`、`cancelCommitteeRun` 风格一致。
- URL 编码位于 API 边界，位置正确且没有双重编码。
- 认证、401 清理、非 2xx 错误映射和 JSON 解析均复用成熟 helper，避免行为分叉。
- 测试使用真实 `Response`，并执行完整的 `deleteCommitteeRun → request → fetch` 路径；不是只验证内部 mock 或复制实现细节，测试具有真实性。
- 测试 `await` 了调用，因此也会暴露响应解析失败；但没有断言解析后的返回值。

## Findings

### Critical

无。

### Important

无。

### Minor

1. `frontend-advisor/src/committee/committeeApi.test.ts:38-52` 未直接验证可选 `AbortSignal` 被传到 `fetch`，也未断言返回的 `{ run_id, deleted }`。生产实现本身正确，且计划指定的测试并未要求这两项，因此不构成 Task 3 的 spec 缺口。后续可在该用例中传入 `controller.signal`，断言 `init?.signal`，并断言函数返回值，以提高回归保护。

## 对实施者 Concerns 的判断

- “未覆盖 404/409 错误映射”不是本任务缺口。Task 3 计划只要求成功请求契约；错误映射由既有 `request()` 统一承担，且本次新增函数没有自定义错误分支。
- “未做端到端联调”不是本任务缺口。Task 3 的交付范围是前端 API 客户端及单元测试，后端联调或页面交互属于后续集成范围。

因此，两项 concerns 都不应阻塞 Task 3 验收。
