# Agent Python 脚本执行 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为主 Agent 增加 `run_python_script` / `register_tool_dataset`，并扩展沙箱契约使无 `result` 时可回传 stdout/stderr，同时保持数据子 Agent 强制 `result` 不回归。

**Architecture:** Runner/Controller 增加 `require_result`；`SandboxClient.execute` 透传该标志并解析扩展响应；主 Agent 侧新增请求级 workspace 与两个工具，配置挂在 `agent_python`；system prompt 明确与 `delegate_data_task` 的分工。

**Tech Stack:** FastAPI、Docker sandbox、pytest、LangChain tools、Pydantic、YAML config。

## Global Constraints

- 结果约定：`result` 优先；主 Agent `require_result=false` 时无 `result` 回传 stdout/stderr。
- 数据子 Agent：`require_result=true`（默认），无 `result` 仍失败。
- 限额：单次 dataset ≤200 行 / ≤200KB；本轮登记 ≤5 个；主 Agent 本轮 Python ≤3 次；超时 30s / 内存 512MB / 输出 1MB。
- 仅请求级临时 dataset，不做跨消息缓存。
- 不新增依赖；不放宽 import/网络/pip；不强制新前端 UI。
- 不要 git commit（除非用户明确要求）。

## File Structure

- `sandbox/runner/entrypoint.py`：`require_result` + stdout/stderr 捕获。
- `sandbox/tests/test_runner.py`：Runner 契约测试。
- `sandbox/controller/app.py`：请求字段、task.json、响应字段、可选 `read_only`。
- `sandbox/tests/test_controller.py`：Controller 契约测试。
- `backend/app/advisor/agent/data_agent/sandbox.py`：`SandboxClient.execute(..., require_result=True)`。
- `backend/app/advisor/agent/python_runtime.py`：主 Agent workspace + 两个工具（新文件）。
- `backend/app/advisor/agent/tools.py`：注册工具。
- `backend/app/advisor/agent/graph.py`：system prompt 规则。
- `backend/app/advisor/config.yaml`：`agent_python` 段。
- `backend/tests/test_agent_python_runtime.py`：主 Agent 工具测试。
- `backend/tests/test_data_agent_sandbox.py`：确认默认 `require_result=true` 不回归。

---

### Task 1: Runner 支持 require_result 与 stdout 回退

**Files:**
- Modify: `sandbox/runner/entrypoint.py`
- Modify: `sandbox/tests/test_runner.py`

**Interfaces:**
- Consumes: existing `execute_task(code, datasets, *, max_output_bytes)`
- Produces: `execute_task(..., *, require_result: bool = True) -> Any`；当 `require_result=False` 且未赋值 `result` 时返回 `{"result": None, "stdout": str, "stderr": str}`；有 `result` 时仍返回原序列化结果（或包含 stdout/stderr 的包装——本任务统一为：有 `result` 时返回值本身保持兼容；stdout 模式返回上述对象）。为让 Controller/Client 稳定解析，**统一成功 payload 形状**：
  - `{"result": <any|null>, "stdout": str, "stderr": str}`
  - 默认 `require_result=True` 且只有 `result` 的旧测试可断言 `payload["result"]`；旧 `execute_task` 调用方若直接拿返回值当业务结果，则在 `require_result=True` 时 **仍只返回业务 result**（兼容 data_agent），`require_result=False` 时返回完整 dict。

- [ ] **Step 1: 写失败测试**

在 `sandbox/tests/test_runner.py` 增加：

```python
def test_execute_task_stdout_fallback_when_result_optional():
    payload = execute_task(
        "print('hello')",
        {},
        max_output_bytes=1024,
        require_result=False,
    )
    assert payload["result"] is None
    assert "hello" in payload["stdout"]
    assert payload["stderr"] == ""


def test_execute_task_still_requires_result_by_default():
    with pytest.raises(ValueError, match="^result_not_assigned$"):
        execute_task("print('x')", {}, max_output_bytes=1024)
```

并保留/更新现有 `test_execute_task_requires_result`。

- [ ] **Step 2: 运行确认失败**

```bash
cd /Users/orange/Desktop/code/share-data
PYTHONPATH=sandbox:backend python -m pytest -q sandbox/tests/test_runner.py -k "stdout_fallback or still_requires_result"
```

Expected: FAIL（无 `require_result` 参数）。

- [ ] **Step 3: 实现最小改动**

在 `execute_task`：

1. 增加 `require_result: bool = True`
2. `exec` 前用 `contextlib.redirect_stdout/redirect_stderr` 接到 `io.StringIO`
3. 若 `"result" in scope`：校验有限数值与输出大小后，当 `require_result` 为 True 时 **返回业务 result**（保持旧行为）；为 False 时返回 `{"result": safe, "stdout": ..., "stderr": ...}`
4. 若无 `result` 且 `require_result`：抛 `result_not_assigned`
5. 若无 `result` 且 not `require_result`：返回 `{"result": None, "stdout": ..., "stderr": ...}`，并按输出字节上限检查编码后大小

`main()` 从 `task.json` 读取 `require_result`（默认 True）传入 `execute_task`。

- [ ] **Step 4: 跑 Runner 测试**

```bash
PYTHONPATH=sandbox:backend python -m pytest -q sandbox/tests/test_runner.py
```

Expected: PASS。

- [ ] **Step 5: 不 commit**

---

### Task 2: Controller 透传 require_result 与响应字段

**Files:**
- Modify: `sandbox/controller/app.py`
- Modify: `sandbox/tests/test_controller.py`

**Interfaces:**
- Consumes: Runner task.json `require_result`
- Produces: `ExecuteRequest.require_result: bool = True`；成功响应可含 `stdout`/`stderr`（可选字段，默认空字符串）；`task.json` 写入该标志；容器 create 增加 `read_only=True`（若现有测试/Docker API 允许）。

- [ ] **Step 1: 写失败测试**

覆盖：

1. 请求带 `require_result=False` 时，写入 runner 的 `task.json` 含 `"require_result": false`
2. 默认请求不带该字段时为 `true`
3. 若有现成 mock executor，断言响应模型接受 `stdout`/`stderr`

参考现有 `test_controller.py` 的 mock Docker 模式，不要起真容器。

- [ ] **Step 2: 运行确认失败**

```bash
PYTHONPATH=sandbox:backend python -m pytest -q sandbox/tests/test_controller.py -k require_result
```

Expected: FAIL。

- [ ] **Step 3: 实现**

```python
class ExecuteRequest(BaseModel):
    ...
    require_result: bool = True

class ExecuteResponse(BaseModel):
    ok: bool
    result: Any | None = None
    stdout: str = ""
    stderr: str = ""
    error: str | None = None
    metrics: dict[str, int]
```

`task.json`：

```python
{
    "code": request.code,
    "max_output_bytes": max_output_bytes,
    "require_result": request.require_result,
}
```

读取 runner `result.json` 时：

- 若为 `{"result": ..., "stdout": ..., "stderr": ...}` 则拆开填响应
- 若为旧式纯业务 JSON，则 `result=payload`, stdout/stderr 空
- `require_result=True` 路径行为与现网一致

`containers.create(...)` 增加 `read_only=True`；若测试因此失败再评估，优先满足安全设计。

- [ ] **Step 4: 跑 Controller 测试**

```bash
PYTHONPATH=sandbox:backend python -m pytest -q sandbox/tests/test_controller.py
```

Expected: PASS。

- [ ] **Step 5: 不 commit**

---

### Task 3: SandboxClient 支持 require_result 与 stdout 解析

**Files:**
- Modify: `backend/app/advisor/agent/data_agent/sandbox.py`
- Modify: `backend/tests/test_data_agent_sandbox.py`

**Interfaces:**
- Consumes: Controller `/v1/execute` 新字段
- Produces: `SandboxClient.execute(code, datasets, limits, *, require_result: bool = True) -> Any`  
  - `require_result=True`：仍返回业务 `result`（data_agent 不改）
  - `require_result=False`：返回 `{"result": Any|None, "stdout": str, "stderr": str}`

- [ ] **Step 1: 写失败测试**

用 httpx MockTransport：

1. 默认调用 body 含 `"require_result": true`（或省略时由服务端默认；客户端应显式传 true 以清晰）
2. `require_result=False` 时返回 dict 含 stdout
3. data_agent 既有成功路径仍拿到业务 result

- [ ] **Step 2: 运行确认失败**

```bash
PYTHONPATH=backend backend/.venv/bin/pytest -q backend/tests/test_data_agent_sandbox.py -k require_result
```

- [ ] **Step 3: 实现**

`body` 增加 `require_result`。响应解析：

```python
if require_result:
    result = payload.get("result")
    _validate_value(result)
    return result
stdout = payload.get("stdout") or ""
stderr = payload.get("stderr") or ""
result = payload.get("result")
if result is not None:
    _validate_value(result)
if not isinstance(stdout, str) or not isinstance(stderr, str):
    raise RuntimeError("sandbox_invalid_output")
return {"result": result, "stdout": stdout, "stderr": stderr}
```

`build_python_tool` 不传 `require_result`（默认 True）。

- [ ] **Step 4: 跑相关测试**

```bash
PYTHONPATH=backend backend/.venv/bin/pytest -q backend/tests/test_data_agent_sandbox.py
```

Expected: PASS。

- [ ] **Step 5: 不 commit**

---

### Task 4: 主 Agent python_runtime 工具与配置

**Files:**
- Create: `backend/app/advisor/agent/python_runtime.py`
- Create: `backend/tests/test_agent_python_runtime.py`
- Modify: `backend/app/advisor/config.yaml`
- Modify: `backend/app/advisor/agent/tools.py`
- Modify: `backend/app/advisor/agent/graph.py`

**Interfaces:**
- Consumes: `SandboxClient.execute(..., require_result=False)`
- Produces:
  - `AgentPythonLimits`（从 `agent_python` 配置加载）
  - `RequestPythonWorkspace`（请求级 datasets + 调用计数）
  - `build_agent_python_tools(user_id) -> list[BaseTool]` 返回
    - `register_tool_dataset(name: str, tool_result_json: str) -> str`
    - `run_python_script(code: str, dataset_ids_json: str = "[]", inline_datasets_json: str = "{}") -> str`

**Dataset 归一化规则：**

1. JSON list[dict] → 直接作为 rows
2. JSON dict → 若含 `items`/`data`/`rows` 且为 list[dict] 用该列表；否则 `[obj]`
3. 其他类型 → error `invalid_dataset_shape`
4. 校验：行数 ≤200、UTF-8 字节 ≤200KB、本轮登记总数 ≤5、id 匹配 `^[A-Za-z0-9_-]+$`

- [ ] **Step 1: 写失败测试**

`backend/tests/test_agent_python_runtime.py` 覆盖：

1. `print` 脚本得到 stdout（mock SandboxClient）
2. 赋值 `result` 得到结构化 result
3. inline 小表可进入 execute datasets
4. register 后再 run 能引用 id
5. 超限（行数/字节/数量/调用次数）拒绝
6. 缺沙箱配置返回 `sandbox_config_missing` 类错误 JSON

- [ ] **Step 2: 运行确认失败**

```bash
PYTHONPATH=backend backend/.venv/bin/pytest -q backend/tests/test_agent_python_runtime.py
```

Expected: FAIL（模块不存在）。

- [ ] **Step 3: 实现配置与模块**

`config.yaml` 追加：

```yaml
agent_python:
  max_rows_per_dataset: 200
  max_bytes_per_dataset: 204800
  max_registered_datasets: 5
  max_python_calls: 3
  sandbox_timeout_seconds: 30
  sandbox_memory_mb: 512
  max_output_bytes: 1048576
```

`python_runtime.py`：请求级 workspace 可用 `contextvars.ContextVar` 或在 `build_tools` 闭包内创建单个 workspace 实例（同一 `build_tools(user_id)` 调用共享）。推荐闭包实例，避免跨请求泄漏。

`run_python_script`：

- `emit_progress(step="sandbox", ...)`
- 合并 inline + registered
- `SandboxClient.from_env().execute(..., require_result=False)`
- 返回 JSON：`{"result": ..., "stdout": ..., "stderr": ...}` 或 error

`tools.py`：`build_tools` 末尾 `extend(build_agent_python_tools(user_id))`。

`graph.py` system prompt 增加规则，例如：

```text
18. 小计算、试跑、对本轮小结果二次加工：使用 run_python_script；
   需要喂入本轮工具 JSON 时先 register_tool_dataset。
   Provider 外部数据/跨源/大表仍用 delegate_data_task（规则 15）。
   解读优先 result，其次 stdout/stderr；禁止编造未工具返回的数据进沙箱。
```

- [ ] **Step 4: 跑测试**

```bash
PYTHONPATH=backend backend/.venv/bin/pytest -q \
  backend/tests/test_agent_python_runtime.py \
  backend/tests/test_data_agent_sandbox.py \
  backend/tests/test_data_agent_delegate.py
```

Expected: PASS。

- [ ] **Step 5: 全量相关回归**

```bash
PYTHONPATH=sandbox:backend backend/.venv/bin/pytest -q \
  sandbox/tests/test_runner.py \
  sandbox/tests/test_controller.py \
  backend/tests/test_agent_python_runtime.py \
  backend/tests/test_data_agent_sandbox.py
```

Expected: PASS。

- [ ] **Step 6: 不 commit**

---

## Spec Coverage Checklist

| 规格项 | 任务 |
|--------|------|
| Runner stdout 回退 / 默认强制 result | Task 1 |
| Controller `require_result` + 响应字段 + read_only | Task 2 |
| SandboxClient 透传 | Task 3 |
| register/run 工具、限额、prompt | Task 4 |
| data_agent 不回归 | Task 3/4 回归 |
| 无跨消息缓存 / 无新 UI | 全局约束，无需代码 |
