# 沙箱 Python 可用性：实用安全集 + 可自愈错误

## 目标

降低 Agent 执行 Python 的无谓失败率，同时保持现有沙箱安全边界：

1. 放宽到「实用安全集」：模型常写、且不打开文件/网络的 import 与 builtins。
2. 把 Runner 已有的错误码与异常类型传到主 Agent / 数据子 Agent，便于按错误改代码。
3. 仍然不回传 traceback、异常原文、源码或表数据。

## 已确认决策

- 方案 B：实用安全集 + 错误回传。
- 不放宽超时 / 内存 / 输出 / 调用次数。
- 不引入 pip、联网、文件 I/O。
- 不回传异常 message / traceback / 源码。
- 不放宽 `result` 的 NaN/Inf 拒绝（本版不做）。

## 架构

```text
Runner
  ├─ 扩大 ALLOWED_IMPORT_ROOTS / SAFE_BUILTINS
  └─ error.json 仍只含 code + 可选 exception_type / line
        │
        ▼
Controller
  └─ 失败响应：error（字符串，兼容）+ 可选 exception_type / line
        │
        ▼
SandboxClient
  └─ SandboxRejected(code, exception_type?, line?)
        │
        ├─ run_python_script（主 Agent）
        └─ run_python_analysis（数据子 Agent）
              共用白名单错误码与中文 message
```

不改 Docker 隔离：无网、非 root、`cap_drop ALL`、`no-new-privileges`、只读根文件系统。

## Runner：import 白名单

`ALLOWED_IMPORT_ROOTS` 精确为：

| 状态 | 根模块 |
|------|--------|
| 已有 | `pandas` `numpy` `math` `statistics` `datetime` `time` `zoneinfo` |
| 新增 | `json` `re` `collections` `itertools` `functools` |

允许 `import json`、`from collections import defaultdict`、`from itertools import groupby` 等；根不在表内仍 `import_not_allowed`。

仍拒绝：`os` `sys` `subprocess` `pathlib` `socket` `importlib` `requests` 以及相对 import。

## Runner：builtins

在现有 `SAFE_BUILTINS` 上**只增加**下列名字（现有项保留）：

| 类 | 新增 |
|----|------|
| 类型判断 | `isinstance` `type` `hasattr` `getattr` |
| 迭代 | `map` `filter` `reversed` `next` `iter` |
| 异常类型 | `Exception` 以及现有 `SAFE_EXCEPTION_TYPES` 中的全部名字 |
| 常用函数 | `repr` `format` `pow` `divmod` `chr` `ord` |

`SAFE_EXCEPTION_TYPES` 现有集合保持不变，并作为可注入的异常类名来源：

`ArithmeticError` `AttributeError` `Exception` `ImportError` `IndexError` `KeyError` `LookupError` `ModuleNotFoundError` `NameError` `RuntimeError` `TypeError` `ValueError` `ZeroDivisionError`

仍不暴露：`open` `eval` `exec` `compile` `input` `setattr` `delattr` `globals` `locals` `vars` `dir`。

`__import__` 继续走现有 `_safe_import`，不得换成内置 `import`。

## Controller：失败响应

保持 `error` 为**字符串**（兼容现有客户端与测试）。失败时允许增加两个可选字段：

| 字段 | 何时出现 | 校验 |
|------|----------|------|
| `exception_type` | `error == "generated_code_failed"` | 必须属于 `SAFE_EXCEPTION_TYPES`；否则省略（当作无此字段） |
| `line` | `error == "syntax_error"` | 必须是正整数；否则省略 |

从 `error.json` **不得**把 `message` 或任何其它字段拷进 HTTP 响应。未知 runner 错误码仍归一成 `runner_failed`，且不带 `exception_type` / `line`。

成功响应形状不变。

## SandboxClient

`SandboxClient.execute` 在 `ok` 为假时：

- `execution_timeout` → 仍 `RuntimeError("sandbox_timeout")`
- `sandbox_failed` → 仍 `RuntimeError("sandbox_unavailable")`
- 其余 → 抛 `SandboxRejected`

`SandboxRejected` 是 `RuntimeError` 子类：

- `str(exc) == "sandbox_rejected:{code}"`（现有 `match="^sandbox_rejected:..."` 测试不破）
- 属性：`code: str`、`exception_type: str | None`、`line: int | None`
- `exception_type` / `line` 仅在通过与 Controller 相同的校验后赋值

## 工具错误 JSON

主 Agent `run_python_script` 与数据子 Agent `run_python_analysis` **共用**同一套 runner 错误码白名单与中文 `message`（以 `data_agent/sandbox.py` 现有 `_ERROR_MESSAGES` / `_SAFE_RUNNER_ERROR_CODES` 为源，避免两套漂移）。

主 Agent 不再把所有 `sandbox_rejected:*` 收成 `sandbox_rejected` / 「计算失败」。白名单内的 runner 码原样返回，例如：

```json
{"error": {"code": "import_not_allowed", "message": "不允许的 import"}}
```

```json
{"error": {"code": "syntax_error", "message": "代码语法错误", "line": 3}}
```

```json
{"error": {"code": "generated_code_failed", "message": "生成代码执行失败：NameError", "exception_type": "NameError"}}
```

规则：

- `code` 在白名单内才原样返回；否则仍 `sandbox_rejected` / 「计算失败」，且不含 `exception_type` / `line`。
- 仅当 `code == "generated_code_failed"` 且 `exception_type` 合法时附加该字段；不得拼接异常原文。
- 仅当 `code == "syntax_error"` 且 `line` 为正整数时附加 `line`。
- 进度事件的 `error_code` 使用上述 `code`（与现在 data_agent 一致）；不把 `exception_type` 或源码写入 progress。

`generated_code_failed` 的 message 统一为下表。数据子 Agent 现有「请用 datasets...」长句不再放进 message（改由 prompt 承担）。

| 条件 | message |
|------|---------|
| `generated_code_failed` 且有类型 | `生成代码执行失败：{exception_type}` |
| `generated_code_failed` 且无类型 | `生成代码执行失败` |
| 其它白名单码 | 沿用现有 `_ERROR_MESSAGES` |

## Prompt / 工具描述

同步更新允许列表，三处文案必须一致：

1. 主 Agent `SYSTEM_PROMPT` 规则 18（`graph.py`）
2. 数据子 Agent system prompt 沙箱约定（`data_agent/graph.py`）
3. `run_python_script` / `run_python_analysis` 的 tool description

写明：

- 已预置 `pd` / `np`；也可 import 白名单根模块。
- 完整允许 import 列表（上表 12 个根）。
- 失败时按 `error.code` / `exception_type` / `line` 改代码，禁止重复同一错误。
- 仍禁止 `read_csv` / 打开文件 / 访问网络。

## 错误处理（安全）

| 场景 | 行为 |
|------|------|
| 非法 import | `import_not_allowed`，无模块名 |
| 语法错误 | `syntax_error` + 可选 `line`，无源码片段 |
| 运行时异常 | `generated_code_failed` + 白名单 `exception_type`，无 message/traceback |
| 自定义/未知异常类名 | Runner 已把类型降为 `Exception`；Controller / 工具层原样透传 `Exception` |
| 超时 / 沙箱不可用 | 保持 `sandbox_timeout` / `sandbox_unavailable` |
| 未知 runner 码 | `sandbox_rejected`，不回传原始码 |

测试必须断言：密钥、表字段值、源码、`Traceback`、`<generated>` 不出现在工具 JSON 与 progress 事件中。

## 测试要点

Runner：

- 新增 5 个根模块均可 import 并产生 `result`。
- 新增 builtins 可调用（至少覆盖 `isinstance` / `json.dumps` / `re.search` / `Exception`）。
- `os` / `subprocess` / 相对 import 仍拒绝。
- `open` / `eval` / `compile` / `input` 仍 `NameError`。
- 失败 `error.json` 仍不含数据值与 traceback（现有 sanitization 测试不破）。

Controller：

- `generated_code_failed` + 合法 `exception_type` 出现在 HTTP 响应。
- 非法 `exception_type` 被丢弃。
- `syntax_error` + 正整数 `line` 出现在响应。
- `error` 字段仍是字符串。

工具层：

- 主 Agent：`import_not_allowed` / `syntax_error` / `generated_code_failed` 不再变成 `sandbox_rejected`。
- 主 Agent：`generated_code_failed` 带 `exception_type`。
- 数据子 Agent：同样带出 `exception_type`；未知码仍 `sandbox_rejected`。
- 两边都不泄漏源码或表数据。

Prompt：相关测试若断言旧 import 列表，改为新列表。

## 验收标准

1. `import json` / `isinstance(x, dict)` / `except Exception` 在沙箱中可运行。
2. 主 Agent 收到的失败不再是一律「计算失败」，至少能区分非法 import、语法错误、运行时类型。
3. 运行时失败能看到 `NameError` / `KeyError` 等白名单类型。
4. 无网、无文件、无 pip、无 traceback/原文 的安全测试全部通过。
5. 超时、内存、输出、调用次数限额不变。

## 非目标

- 放宽 NaN/Inf
- 提高 30s / 512MB / 1MB / 主 Agent 3 次调用
- `pathlib` / `os` / 只读文件 / pip / 联网
- 把完整 traceback 或异常 message 回给模型
- 跨消息 dataset 缓存
- 前端新 UI

## 实现落点

| 区域 | 路径 |
|------|------|
| Runner 白名单 | `sandbox/runner/entrypoint.py` |
| Controller 透传 | `sandbox/controller/app.py` |
| 客户端异常 | `backend/app/advisor/agent/data_agent/sandbox.py` |
| 主 Agent 映射 | `backend/app/advisor/agent/python_runtime.py` |
| Prompt | `backend/app/advisor/agent/graph.py`、`data_agent/graph.py` |
| 测试 | `sandbox/tests/test_runner.py`、`sandbox/tests/test_controller.py`、`backend/tests/test_agent_python_runtime.py`、`backend/tests/test_data_agent_sandbox.py`、prompt 相关测试 |
