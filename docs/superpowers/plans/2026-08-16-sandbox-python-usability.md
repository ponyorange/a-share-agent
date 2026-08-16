# 沙箱 Python 可用性 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把沙箱放宽到实用安全集（json/re/collections 等 + isinstance/Exception），并把 Runner 错误码 / 异常类型传到主 Agent 与数据子 Agent，便于按错误改代码。

**Architecture:** Runner 扩大 import/builtins 白名单；Controller 失败响应在字符串 `error` 之外透传校验过的 `exception_type`/`line`；`SandboxClient` 抛 `SandboxRejected`；两个 Python 工具共用同一套错误 JSON 映射。

**Tech Stack:** pytest、Docker sandbox runner/controller、LangChain tools、httpx。

**Spec:** `docs/superpowers/specs/2026-08-16-sandbox-python-usability-design.md`

## Global Constraints

- 方案 B：实用安全集 + 错误回传；不放宽超时 30s / 内存 512MB / 输出 1MB / 主 Agent 3 次调用。
- 不引入 pip、联网、文件 I/O；仍拒绝 `open`/`eval`/`os`/`subprocess`。
- 不回传异常 message / traceback / 源码 / 表数据。
- `error` HTTP 字段保持字符串；`exception_type` 必须属于 Runner 现有 `SAFE_EXCEPTION_TYPES`。
- `generated_code_failed` message：有类型时为 `生成代码执行失败：{exception_type}`，否则 `生成代码执行失败`。
- 允许 import 根（12 个）：`pandas` `numpy` `math` `statistics` `datetime` `time` `zoneinfo` `json` `re` `collections` `itertools` `functools`。
- Prompt / tool description 三处允许列表必须一致，使用：`pandas/numpy/math/statistics/datetime/time/zoneinfo/json/re/collections/itertools/functools`。
- 不要 git commit（除非用户明确要求）。

## File Structure

- `sandbox/runner/entrypoint.py`：扩大 `ALLOWED_IMPORT_ROOTS` / `SAFE_BUILTINS`。
- `sandbox/tests/test_runner.py`：新 import / builtins 与拒绝项。
- `sandbox/controller/app.py`：失败响应透传 `exception_type` / `line`。
- `sandbox/tests/test_controller.py`：透传与丢弃非法类型。
- `backend/app/advisor/agent/data_agent/sandbox.py`：`SandboxRejected`、客户端解析、共用错误 JSON。
- `backend/app/advisor/agent/python_runtime.py`：主 Agent 改用共用映射。
- `backend/app/advisor/agent/graph.py`、`data_agent/graph.py`：prompt。
- `backend/tests/test_data_agent_sandbox.py`、`test_agent_python_runtime.py`、`test_data_agent_delegate.py`。

---

### Task 1: Runner 实用安全集

**Files:**
- Modify: `sandbox/runner/entrypoint.py`
- Modify: `sandbox/tests/test_runner.py`

**Interfaces:**
- Consumes: 现有 `execute_task` / `ALLOWED_IMPORT_ROOTS` / `SAFE_BUILTINS` / `SAFE_EXCEPTION_TYPES`
- Produces: 12 个允许根；builtins 新增 `isinstance` `type` `hasattr` `getattr` `map` `filter` `reversed` `next` `iter` `repr` `format` `pow` `divmod` `chr` `ord` 以及 `SAFE_EXCEPTION_TYPES` 中全部异常类。`__import__` 仍为 `_safe_import`。

- [ ] **Step 1: 写失败测试**

在 `sandbox/tests/test_runner.py` 的 `test_execute_task_allows_every_declared_import` 参数表追加：

```python
        ("import json\nresult = json.dumps({'a': 1})", '{"a": 1}'),
        ("import re\nresult = bool(re.search(r'\\\\d', 'a1'))", True),
        ("from collections import Counter\nresult = Counter('ab')['a']", 1),
        ("from itertools import chain\nresult = list(chain([1], [2]))", [1, 2]),
        ("from functools import reduce\nresult = reduce(lambda a, b: a + b, [1, 2], 0)", 3),
```

再新增：

```python
def test_execute_task_exposes_usability_builtins():
    code = (
        "result = {"
        "'isinstance': isinstance({'a': 1}, dict), "
        "'map': list(map(lambda x: x + 1, [1])), "
        "'pow': pow(2, 3)"
        "}\n"
        "try:\n"
        "    raise ValueError('x')\n"
        "except Exception as exc:\n"
        "    result['exc'] = type(exc).__name__"
    )
    assert execute_task(code, {}, max_output_bytes=4096) == {
        "isinstance": True,
        "map": [2],
        "pow": 8,
        "exc": "ValueError",
    }
```

现有 `test_execute_task_rejects_disallowed_import` 与 `test_execute_task_does_not_expose_dangerous_builtins` 保持不变。

- [ ] **Step 2: 运行确认失败**

```bash
cd /Users/orange/Desktop/code/share-data
PYTHONPATH=sandbox:backend python -m pytest -q sandbox/tests/test_runner.py -k "allows_every_declared_import or exposes_usability_builtins"
```

Expected: FAIL（`import_not_allowed` 或 `NameError` / `GeneratedCodeError`）。

- [ ] **Step 3: 最小实现**

`ALLOWED_IMPORT_ROOTS` 增加：`json` `re` `collections` `itertools` `functools`。

`SAFE_BUILTINS` 增加 spec 列出的名字，异常类从 `SAFE_EXCEPTION_TYPES` 映射到内置类（`{"Exception": Exception, "ValueError": ValueError, ...}`），不要手写漏项。不要加入 `open`/`eval`/`exec`/`compile`/`input`/`setattr`/`delattr`。

- [ ] **Step 4: 跑测试**

```bash
PYTHONPATH=sandbox:backend python -m pytest -q sandbox/tests/test_runner.py
```

Expected: PASS。

---

### Task 2: Controller 透传 exception_type / line

**Files:**
- Modify: `sandbox/controller/app.py`
- Modify: `sandbox/tests/test_controller.py`

**Interfaces:**
- Consumes: `error.json` 的 `error` / `exception_type` / `line`；现有 `SAFE_RUNNER_ERRORS`
- Produces: 失败 HTTP 体仍为 `error: str`；当 `error=="generated_code_failed"` 且类型在 `SAFE_EXCEPTION_TYPES` 时带 `exception_type`；当 `error=="syntax_error"` 且 `line` 为正整数时带 `line`。绝不拷贝 `message`。

- [ ] **Step 1: 写失败测试**

在 `sandbox/tests/test_controller.py` 追加（复用 `_archive` / `_executor` / `REQUEST`）：

```python
def test_executor_forwards_exception_type_for_generated_code_failed():
    error = {
        "error": "generated_code_failed",
        "message": "generated_code_failed TOKEN-secret",
        "exception_type": "KeyError",
    }
    container = FakeContainer(
        wait_result={"StatusCode": 1},
        archives={"/output/error.json": _archive("error.json", error)},
    )
    executor, _ = _executor(container)
    response = executor.execute(controller.ExecuteRequest(**REQUEST))
    assert response["ok"] is False
    assert response["error"] == "generated_code_failed"
    assert isinstance(response["error"], str)
    assert response["exception_type"] == "KeyError"
    assert "TOKEN-secret" not in json.dumps(response)


def test_executor_drops_unknown_exception_type():
    error = {
        "error": "generated_code_failed",
        "exception_type": "SecretTokenError",
    }
    container = FakeContainer(
        wait_result={"StatusCode": 1},
        archives={"/output/error.json": _archive("error.json", error)},
    )
    executor, _ = _executor(container)
    response = executor.execute(controller.ExecuteRequest(**REQUEST))
    assert response["error"] == "generated_code_failed"
    assert "exception_type" not in response
    assert "SecretTokenError" not in json.dumps(response)


def test_executor_forwards_syntax_error_line():
    error = {"error": "syntax_error", "message": "syntax_error", "line": 3}
    container = FakeContainer(
        wait_result={"StatusCode": 1},
        archives={"/output/error.json": _archive("error.json", error)},
    )
    executor, _ = _executor(container)
    response = executor.execute(controller.ExecuteRequest(**REQUEST))
    assert response["error"] == "syntax_error"
    assert response["line"] == 3
```

- [ ] **Step 2: 运行确认失败**

```bash
PYTHONPATH=sandbox python -m pytest -q sandbox/tests/test_controller.py -k "forwards_exception_type or drops_unknown_exception or forwards_syntax_error_line"
```

Expected: FAIL（响应无 `exception_type` / `line`）。

- [ ] **Step 3: 最小实现**

在 `sandbox/controller/app.py` 增加与 Runner 相同的 `SAFE_EXCEPTION_TYPES` 集合。

扩展内部 `response(...)`：失败时按 spec 校验后写入可选字段。读取 `error.json` 后把 `exception_type`/`line` 传入 `response`。不要把 `message` 写入 HTTP 体。未知 runner 码仍归一 `runner_failed` 且不带这两个字段。

现有 `test_executor_reads_sanitized_error_archive_without_docker_logs` 可继续只断言 `error` 字符串；若实现带上了 `exception_type`，该测试仍应通过（它没禁止多字段）。

- [ ] **Step 4: 跑测试**

```bash
PYTHONPATH=sandbox python -m pytest -q sandbox/tests/test_controller.py
```

Expected: PASS。

---

### Task 3: SandboxRejected 与客户端解析

**Files:**
- Modify: `backend/app/advisor/agent/data_agent/sandbox.py`
- Modify: `backend/tests/test_data_agent_sandbox.py`

**Interfaces:**
- Consumes: Controller 失败 JSON：`error` 字符串 + 可选 `exception_type` / `line`
- Produces:

```python
class SandboxRejected(RuntimeError):
    def __init__(
        self,
        code: str,
        *,
        exception_type: str | None = None,
        line: int | None = None,
    ) -> None: ...
    # str(self) == f"sandbox_rejected:{code}"
    # .code / .exception_type / .line
```

`execution_timeout` / `sandbox_failed` 仍分别抛 `RuntimeError("sandbox_timeout")` / `RuntimeError("sandbox_unavailable")`。其余失败抛 `SandboxRejected`。`exception_type` 仅当属于与 Controller 相同的 `SAFE_EXCEPTION_TYPES` 时赋值；`line` 仅当为正整数时赋值。

- [ ] **Step 1: 写失败测试**

在 `backend/tests/test_data_agent_sandbox.py` 追加：

```python
from app.advisor.agent.data_agent.sandbox import SandboxClient, SandboxRejected


def test_sandbox_client_attaches_exception_type_and_line():
    def handler(_request):
        return httpx.Response(
            200,
            json={
                "ok": False,
                "error": "generated_code_failed",
                "exception_type": "NameError",
                "metrics": {"elapsed_ms": 1},
            },
        )

    client = SandboxClient("http://sandbox", "token", transport=httpx.MockTransport(handler))
    with pytest.raises(SandboxRejected) as caught:
        client.execute("result={}", {}, DataAgentLimits())
    assert str(caught.value) == "sandbox_rejected:generated_code_failed"
    assert caught.value.code == "generated_code_failed"
    assert caught.value.exception_type == "NameError"
    assert caught.value.line is None


def test_sandbox_client_attaches_syntax_line():
    def handler(_request):
        return httpx.Response(
            200,
            json={"ok": False, "error": "syntax_error", "line": 4, "metrics": {"elapsed_ms": 1}},
        )

    client = SandboxClient("http://sandbox", "token", transport=httpx.MockTransport(handler))
    with pytest.raises(SandboxRejected) as caught:
        client.execute("result={}", {}, DataAgentLimits())
    assert caught.value.code == "syntax_error"
    assert caught.value.line == 4


def test_sandbox_client_drops_unknown_exception_type():
    def handler(_request):
        return httpx.Response(
            200,
            json={
                "ok": False,
                "error": "generated_code_failed",
                "exception_type": "SecretTokenError",
                "metrics": {"elapsed_ms": 1},
            },
        )

    client = SandboxClient("http://sandbox", "token", transport=httpx.MockTransport(handler))
    with pytest.raises(SandboxRejected) as caught:
        client.execute("result={}", {}, DataAgentLimits())
    assert caught.value.exception_type is None
    assert "SecretTokenError" not in str(caught.value)
```

现有 `test_sandbox_client_maps_controller_string_runner_rejection_code` 继续用 `pytest.raises(RuntimeError, match="^sandbox_rejected:invalid_dataset_name$")`（子类仍匹配）。

- [ ] **Step 2: 运行确认失败**

```bash
cd /Users/orange/Desktop/code/share-data
PYTHONPATH=backend backend/.venv/bin/pytest -q backend/tests/test_data_agent_sandbox.py -k "attaches_exception_type or attaches_syntax_line or drops_unknown_exception"
```

Expected: FAIL（无 `SandboxRejected` 或无属性）。

- [ ] **Step 3: 最小实现**

在 `sandbox.py` 增加 `SAFE_EXCEPTION_TYPES`（与 Runner 相同集合）和 `SandboxRejected`。`execute` 解析失败响应时：字符串 `error` 仍作 code；从 payload 顶层读 `exception_type`/`line`（不是从 `error` 对象里，因为 Controller 保持 `error` 为字符串）。校验后构造 `SandboxRejected`。

- [ ] **Step 4: 跑测试**

```bash
PYTHONPATH=backend backend/.venv/bin/pytest -q backend/tests/test_data_agent_sandbox.py
```

Expected: PASS。

---

### Task 4: 共用工具错误 JSON（数据子 Agent）

**Files:**
- Modify: `backend/app/advisor/agent/data_agent/sandbox.py`
- Modify: `backend/tests/test_data_agent_sandbox.py`

**Interfaces:**
- Consumes: `SandboxRejected` / 其它 `RuntimeError`
- Produces: `format_sandbox_tool_error(exc: RuntimeError) -> str`，返回 JSON 文本：

```json
{"error": {"code": "...", "message": "...", "exception_type"?: "...", "line"?: 3}}
```

映射规则：

- 用现有 `_map_runtime_error_code` 得 `code`。
- `generated_code_failed` + 合法 `exception_type` → message `生成代码执行失败：{exception_type}`，并带字段。
- `generated_code_failed` 无类型 → message `生成代码执行失败`。
- `syntax_error` + 正整数 `line` → 带 `line`。
- 其它白名单码 → 现有 `_ERROR_MESSAGES`。
- 未知码 → `sandbox_rejected` / `计算失败`，无额外字段。

`run_python_analysis` 的 `except RuntimeError` 改为调用 `format_sandbox_tool_error`。`_ERROR_MESSAGES["generated_code_failed"]` 改为 `生成代码执行失败`。

- [ ] **Step 1: 写失败测试**

```python
def test_python_analysis_tool_surfaces_exception_type(tmp_path):
    def handler(_request):
        return httpx.Response(
            200,
            json={
                "ok": False,
                "error": "generated_code_failed",
                "exception_type": "NameError",
                "metrics": {"elapsed_ms": 1},
            },
        )

    with DatasetWorkspace(DataAgentLimits(), root=tmp_path / "r") as workspace:
        meta = workspace.create_dataset(
            "akshare",
            "demo",
            {},
            {
                "columns": ["x"],
                "rows": [{"x": 1}],
                "returned": 1,
                "total": 1,
                "truncated": False,
            },
        )
        client = SandboxClient(
            "http://sandbox", "token", transport=httpx.MockTransport(handler)
        )
        tool = build_python_tool(workspace, client)
        payload = json.loads(
            tool.invoke(
                {
                    "code": "missing_name",
                    "dataset_ids_json": json.dumps([meta.dataset_id]),
                }
            )
        )

    assert payload == {
        "error": {
            "code": "generated_code_failed",
            "message": "生成代码执行失败：NameError",
            "exception_type": "NameError",
        }
    }
    assert "missing_name" not in json.dumps(payload)
```

现有 `test_python_analysis_tool_surfaces_allowlisted_runner_errors` 对 `generated_code_failed` 只断言 code 与 message 非空，仍应通过。

- [ ] **Step 2: 运行确认失败**

```bash
PYTHONPATH=backend backend/.venv/bin/pytest -q backend/tests/test_data_agent_sandbox.py -k surfaces_exception_type
```

Expected: FAIL（工具 JSON 无 `exception_type` 或 message 仍是旧长句）。

- [ ] **Step 3: 最小实现**

实现 `format_sandbox_tool_error`，`run_python_analysis` 失败分支使用它。progress 的 `error_code` 仍用映射后的 `code`，不要写入 `exception_type`。

- [ ] **Step 4: 跑测试**

```bash
PYTHONPATH=backend backend/.venv/bin/pytest -q backend/tests/test_data_agent_sandbox.py
```

Expected: PASS。

---

### Task 5: 主 Agent run_python_script 使用共用映射

**Files:**
- Modify: `backend/app/advisor/agent/python_runtime.py`
- Modify: `backend/tests/test_agent_python_runtime.py`

**Interfaces:**
- Consumes: `format_sandbox_tool_error`、`SandboxRejected`（从 `data_agent.sandbox` 导入）
- Produces: `run_python_script` 失败时与数据子 Agent 相同形状的 error JSON；progress `error_code` 为映射后的 code。

- [ ] **Step 1: 写失败测试**

```python
def test_run_python_script_surfaces_import_and_runtime_errors(monkeypatch):
    def handler(_request):
        return httpx.Response(
            200,
            json={
                "ok": False,
                "error": "import_not_allowed",
                "metrics": {"elapsed_ms": 1},
            },
        )

    _patch_client(monkeypatch, handler)
    tools = {t.name: t for t in build_agent_python_tools("u1")}
    payload = json.loads(tools["run_python_script"].invoke({"code": "import os"}))
    assert payload == {
        "error": {"code": "import_not_allowed", "message": "不允许的 import"}
    }


def test_run_python_script_surfaces_exception_type(monkeypatch):
    def handler(_request):
        return httpx.Response(
            200,
            json={
                "ok": False,
                "error": "generated_code_failed",
                "exception_type": "NameError",
                "metrics": {"elapsed_ms": 1},
            },
        )

    _patch_client(monkeypatch, handler)
    tools = {t.name: t for t in build_agent_python_tools("u1")}
    payload = json.loads(tools["run_python_script"].invoke({"code": "missing"}))
    assert payload["error"]["code"] == "generated_code_failed"
    assert payload["error"]["exception_type"] == "NameError"
    assert payload["error"]["message"] == "生成代码执行失败：NameError"
    assert "missing" not in json.dumps(payload)


def test_run_python_script_unknown_runner_code_stays_rejected(monkeypatch):
    def handler(_request):
        return httpx.Response(
            200,
            json={"ok": False, "error": "unknown_internal_detail", "metrics": {"elapsed_ms": 1}},
        )

    _patch_client(monkeypatch, handler)
    tools = {t.name: t for t in build_agent_python_tools("u1")}
    payload = json.loads(tools["run_python_script"].invoke({"code": "print(1)"}))
    assert payload == {"error": {"code": "sandbox_rejected", "message": "计算失败"}}
    assert "unknown_internal_detail" not in json.dumps(payload)
```

- [ ] **Step 2: 运行确认失败**

```bash
PYTHONPATH=backend backend/.venv/bin/pytest -q backend/tests/test_agent_python_runtime.py -k "surfaces_import or surfaces_exception_type or unknown_runner_code"
```

Expected: FAIL（主 Agent 仍返回 `sandbox_rejected` / 「计算失败」）。

- [ ] **Step 3: 最小实现**

删除 `python_runtime._map_runtime_error` 把一切收成 `sandbox_rejected` 的逻辑。`except RuntimeError` 改为 `format_sandbox_tool_error(exc)`，progress `error_code` 用解析后的 `code`。

- [ ] **Step 4: 跑测试**

```bash
PYTHONPATH=backend backend/.venv/bin/pytest -q \
  backend/tests/test_agent_python_runtime.py \
  backend/tests/test_data_agent_sandbox.py
```

Expected: PASS。

---

### Task 6: Prompt 与 tool description

**Files:**
- Modify: `backend/app/advisor/agent/graph.py`（规则 18）
- Modify: `backend/app/advisor/agent/data_agent/graph.py`（沙箱约定）
- Modify: `backend/app/advisor/agent/python_runtime.py`（`run_python_script` description）
- Modify: `backend/app/advisor/agent/data_agent/sandbox.py`（`run_python_analysis` description）
- Modify: `backend/tests/test_data_agent_delegate.py`
- Modify: `backend/tests/test_data_agent_sandbox.py`（`test_python_analysis_tool_description_documents_datasets_contract`）

**Interfaces:**
- Consumes: 上表 12 个根模块
- Produces: 三处文案均含
  `pandas/numpy/math/statistics/datetime/time/zoneinfo/json/re/collections/itertools/functools`
  以及「按 error.code / exception_type / line 改代码」。

- [ ] **Step 1: 写失败测试**

在 `test_main_agent_registers_delegate_last_and_preserves_specialized_rules` 追加：

```python
    assert "json/re/collections/itertools/functools" in SYSTEM_PROMPT
    assert "exception_type" in SYSTEM_PROMPT
```

在 `test_python_analysis_tool_description_documents_datasets_contract` 追加：

```python
    assert "json/re/collections/itertools/functools" in tool.description
    assert "exception_type" in tool.description
```

- [ ] **Step 2: 运行确认失败**

```bash
PYTHONPATH=backend backend/.venv/bin/pytest -q \
  backend/tests/test_data_agent_delegate.py::test_main_agent_registers_delegate_last_and_preserves_specialized_rules \
  backend/tests/test_data_agent_sandbox.py::test_python_analysis_tool_description_documents_datasets_contract
```

Expected: FAIL（旧允许列表）。

- [ ] **Step 3: 改文案**

规则 18 改为（保持现有分工句，只换允许列表并补错误指引）：

```text
18. 小计算、试跑、对本轮小结果二次加工：使用 run_python_script；
   需要喂入本轮工具 JSON 时先 register_tool_dataset。
   Provider 外部数据/跨源/大表仍用 delegate_data_task（规则 15）。
   沙箱已预置 pd/np（可直接用或 import pandas/numpy）；
   仅允许 pandas/numpy/math/statistics/datetime/time/zoneinfo/json/re/collections/itertools/functools。
   失败时按 error.code / exception_type / line 改代码，禁止重复同一错误。
   解读优先 result，其次 stdout/stderr；禁止编造未工具返回的数据进沙箱。
```

数据子 Agent prompt：

```text
仅允许 import：pandas/numpy/math/statistics/datetime/time/zoneinfo/json/re/collections/itertools/functools；其它会 import_not_allowed。
若返回 import_not_allowed / result_not_assigned / syntax_error / generated_code_failed，按 error.code / exception_type / line 改代码，勿重复同一错误。
```

两个 tool description 同步上述允许列表与错误指引；仍写禁止 `read_csv` / 打开文件 / 访问网络。

- [ ] **Step 4: 回归**

```bash
PYTHONPATH=sandbox:backend python -m pytest -q sandbox/tests/test_runner.py sandbox/tests/test_controller.py
PYTHONPATH=backend backend/.venv/bin/pytest -q \
  backend/tests/test_agent_python_runtime.py \
  backend/tests/test_data_agent_sandbox.py \
  backend/tests/test_data_agent_delegate.py
```

Expected: 全部 PASS。
