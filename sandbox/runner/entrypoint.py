"""Execute one generated Python data-analysis task inside the sandbox container."""

from __future__ import annotations

import ast
import importlib
import json
import math
import os
import tempfile
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

try:
    from serialize import json_safe
except ModuleNotFoundError:  # Local tests run from the repository root.
    from backend.app.serialize import json_safe


TASK_PATH = Path("/input/task.json")
DATASETS_DIR = Path("/input/datasets")
OUTPUT_DIR = Path("/output")
DEFAULT_MAX_OUTPUT_BYTES = 1_048_576

ALLOWED_IMPORT_ROOTS = {"pandas", "numpy", "math", "statistics", "datetime"}


def _safe_import(
    name: str,
    globals: dict[str, Any] | None = None,
    locals: dict[str, Any] | None = None,
    fromlist: tuple[str, ...] = (),
    level: int = 0,
) -> Any:
    del globals, locals
    root = name.split(".", 1)[0]
    if level != 0 or root not in ALLOWED_IMPORT_ROOTS:
        raise ValueError(f"import_not_allowed: {root}")
    module = importlib.import_module(name)
    return module if fromlist else importlib.import_module(root)


SAFE_BUILTINS = {
    "__import__": _safe_import,
    "abs": abs,
    "all": all,
    "any": any,
    "bool": bool,
    "dict": dict,
    "enumerate": enumerate,
    "float": float,
    "int": int,
    "len": len,
    "list": list,
    "max": max,
    "min": min,
    "range": range,
    "round": round,
    "set": set,
    "sorted": sorted,
    "str": str,
    "sum": sum,
    "tuple": tuple,
    "zip": zip,
}


def _validate_imports(tree: ast.AST) -> None:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
            level = 0
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""]
            level = node.level
        else:
            continue

        for name in names:
            root = name.split(".", 1)[0]
            if level != 0 or root not in ALLOWED_IMPORT_ROOTS:
                raise ValueError(f"import_not_allowed: {root}")


def _reject_non_finite(value: Any) -> None:
    if isinstance(value, (float, np.floating)) and not math.isfinite(float(value)):
        raise ValueError("result_not_finite")
    if isinstance(value, dict):
        for key, item in value.items():
            _reject_non_finite(key)
            _reject_non_finite(item)
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            _reject_non_finite(item)
    elif isinstance(value, pd.Series):
        for item in value.array:
            _reject_non_finite(item)
    elif isinstance(value, pd.DataFrame):
        for item in value.to_numpy().flat:
            _reject_non_finite(item)


def _normalize_numpy_scalars(value: Any) -> Any:
    if isinstance(value, np.floating):
        return round(float(value), 15)
    if isinstance(value, dict):
        return {
            _normalize_numpy_scalars(key): _normalize_numpy_scalars(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_normalize_numpy_scalars(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_normalize_numpy_scalars(item) for item in value)
    return value


def execute_task(
    code: str,
    raw_datasets: dict[str, list[dict[str, Any]]],
    *,
    max_output_bytes: int,
) -> Any:
    tree = ast.parse(code, mode="exec")
    _validate_imports(tree)
    datasets = {key: pd.DataFrame(rows) for key, rows in raw_datasets.items()}
    scope = {
        "datasets": datasets,
        "pd": pd,
        "np": np,
        "__builtins__": SAFE_BUILTINS,
    }
    exec(compile(tree, "<generated>", "exec"), scope, scope)
    if "result" not in scope:
        raise ValueError("result_not_assigned")

    _reject_non_finite(scope["result"])
    safe = json_safe(_normalize_numpy_scalars(scope["result"]))
    encoded = json.dumps(safe, ensure_ascii=False, allow_nan=False).encode("utf-8")
    if len(encoded) > max_output_bytes:
        raise ValueError("output_too_large")
    return safe


def _atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as temporary:
        temporary_path = Path(temporary.name)
        try:
            json.dump(payload, temporary, ensure_ascii=False, allow_nan=False)
            temporary.flush()
            os.fsync(temporary.fileno())
        except BaseException:
            temporary_path.unlink(missing_ok=True)
            raise
    os.replace(temporary_path, path)


def _load_datasets() -> dict[str, list[dict[str, Any]]]:
    datasets: dict[str, list[dict[str, Any]]] = {}
    for path in sorted(DATASETS_DIR.glob("*.json")):
        with path.open(encoding="utf-8") as source:
            datasets[path.stem] = json.load(source)
    return datasets


def main() -> int:
    try:
        with TASK_PATH.open(encoding="utf-8") as source:
            task = json.load(source)
        result = execute_task(
            task["code"],
            _load_datasets(),
            max_output_bytes=task.get(
                "max_output_bytes",
                DEFAULT_MAX_OUTPUT_BYTES,
            ),
        )
        _atomic_write_json(OUTPUT_DIR / "result.json", result)
        (OUTPUT_DIR / "error.json").unlink(missing_ok=True)
        return 0
    except Exception as exc:
        error = {
            "error": type(exc).__name__,
            "message": str(exc)[:500],
        }
        _atomic_write_json(OUTPUT_DIR / "error.json", error)
        (OUTPUT_DIR / "result.json").unlink(missing_ok=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
