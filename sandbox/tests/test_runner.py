import json
from pathlib import Path

import pytest

from runner import entrypoint
from runner.entrypoint import execute_task


def test_execute_task_exposes_dataframes_and_requires_result():
    datasets = {"abc": [{"close": 10}, {"close": 12}]}

    result = execute_task(
        "result = {'return': datasets['abc']['close'].iloc[-1] / "
        "datasets['abc']['close'].iloc[0] - 1}",
        datasets,
        max_output_bytes=1024,
    )

    assert result == {"return": 0.2}


@pytest.mark.parametrize(
    ("statement", "expected"),
    [
        ("import pandas\nresult = pandas.__name__", "pandas"),
        ("import numpy\nresult = numpy.__name__", "numpy"),
        ("import math\nresult = math.sqrt(9)", 3.0),
        ("import statistics\nresult = statistics.mean([2, 4])", 3),
        ("import datetime\nresult = datetime.date(2026, 7, 24)", "2026-07-24"),
        ("from math import sqrt\nresult = sqrt(16)", 4.0),
    ],
)
def test_execute_task_allows_every_declared_import(statement: str, expected):
    assert execute_task(statement, {}, max_output_bytes=1024) == expected


def test_execute_task_exposes_common_safe_builtins():
    code = (
        "result = {"
        "'abs': abs(-2), 'all': all([True]), 'any': any([False, True]), "
        "'bool': bool(1), 'dict': dict(a=1), 'enumerate': list(enumerate(['a'])), "
        "'float': float(2), 'int': int('3'), 'len': len([1]), "
        "'list': list((1,)), 'max': max([1, 2]), 'min': min([1, 2]), "
        "'range': list(range(2)), 'round': round(1.2), 'set': sorted(set([2, 1])), "
        "'sorted': sorted([2, 1]), 'str': str(3), 'sum': sum([1, 2]), "
        "'tuple': list(tuple([1])), 'zip': list(zip([1], [2]))}"
    )

    result = execute_task(code, {}, max_output_bytes=4096)

    assert result["sum"] == 3
    assert result["zip"] == [[1, 2]]
    assert set(result) == {
        "abs",
        "all",
        "any",
        "bool",
        "dict",
        "enumerate",
        "float",
        "int",
        "len",
        "list",
        "max",
        "min",
        "range",
        "round",
        "set",
        "sorted",
        "str",
        "sum",
        "tuple",
        "zip",
    }


@pytest.mark.parametrize(
    "code",
    [
        "import subprocess\nresult = {}",
        "from os import environ\nresult = {}",
        "from . import sibling\nresult = {}",
    ],
)
def test_execute_task_rejects_disallowed_import(code: str):
    with pytest.raises(ValueError, match="^import_not_allowed$"):
        execute_task(code, {}, max_output_bytes=1024)


def test_execute_task_requires_result():
    with pytest.raises(ValueError, match="^result_not_assigned$"):
        execute_task("value = 1", {}, max_output_bytes=1024)


@pytest.mark.parametrize("expression", ["float('nan')", "float('inf')", "-float('inf')"])
def test_execute_task_rejects_non_finite_numbers(expression: str):
    with pytest.raises(ValueError, match="^result_not_finite$"):
        execute_task(f"result = {expression}", {}, max_output_bytes=1024)


def test_execute_task_rejects_oversized_output_at_exact_boundary():
    with pytest.raises(ValueError, match="^output_too_large$"):
        execute_task("result = 'é'", {}, max_output_bytes=3)


@pytest.mark.parametrize("forbidden", ["open", "eval", "compile", "input"])
def test_execute_task_does_not_expose_dangerous_builtins(forbidden: str):
    with pytest.raises(entrypoint.GeneratedCodeError) as caught:
        execute_task(f"result = {forbidden}", {}, max_output_bytes=1024)
    assert caught.value.exception_type == "NameError"


def _configure_main_paths(monkeypatch, tmp_path: Path):
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    datasets_dir = input_dir / "datasets"
    datasets_dir.mkdir(parents=True)
    output_dir.mkdir()
    monkeypatch.setattr(entrypoint, "TASK_PATH", input_dir / "task.json")
    monkeypatch.setattr(entrypoint, "DATASETS_DIR", datasets_dir)
    monkeypatch.setattr(entrypoint, "OUTPUT_DIR", output_dir)
    return input_dir, output_dir, datasets_dir


def test_main_atomically_writes_success(monkeypatch, tmp_path: Path):
    input_dir, output_dir, datasets_dir = _configure_main_paths(monkeypatch, tmp_path)
    (input_dir / "task.json").write_text(
        json.dumps({"code": "result = len(datasets['prices'])", "max_output_bytes": 100}),
        encoding="utf-8",
    )
    (datasets_dir / "prices.json").write_text('[{"close": 12}]', encoding="utf-8")
    replacements = []
    real_replace = entrypoint.os.replace

    def recording_replace(source, target):
        replacements.append((Path(source), Path(target)))
        real_replace(source, target)

    monkeypatch.setattr(entrypoint.os, "replace", recording_replace)

    assert entrypoint.main() == 0
    assert json.loads((output_dir / "result.json").read_text()) == 1
    assert not (output_dir / "error.json").exists()
    assert replacements[-1][1] == output_dir / "result.json"
    assert replacements[-1][0] != replacements[-1][1]
    assert not replacements[-1][0].exists()


def test_main_atomically_writes_sanitized_failure(monkeypatch, tmp_path: Path):
    input_dir, output_dir, _ = _configure_main_paths(monkeypatch, tmp_path)
    secret = "TOKEN-sk_live_DO_NOT_LEAK"
    large_table_value = "PRIVATE_TABLE_VALUE_" + ("x" * 2000)
    datasets = [{"secret": secret, "payload": large_table_value} for _ in range(50)]
    (input_dir / "datasets" / "huge.json").write_text(
        json.dumps(datasets),
        encoding="utf-8",
    )
    (input_dir / "task.json").write_text(
        json.dumps(
            {
                "code": "result = {}[datasets['huge'].to_string()]",
                "max_output_bytes": 100,
            }
        ),
        encoding="utf-8",
    )

    assert entrypoint.main() == 1
    error = json.loads((output_dir / "error.json").read_text())
    assert error == {
        "error": "generated_code_failed",
        "message": "generated_code_failed",
        "exception_type": "KeyError",
    }
    encoded_error = json.dumps(error)
    assert secret not in encoded_error
    assert large_table_value not in encoded_error
    assert "huge" not in encoded_error
    assert "Traceback" not in encoded_error
    assert "<generated>" not in encoded_error
    assert not (output_dir / "result.json").exists()
    assert not list(output_dir.glob("*.tmp"))


def test_main_hard_caps_requested_output_limit(monkeypatch, tmp_path: Path):
    input_dir, output_dir, _ = _configure_main_paths(monkeypatch, tmp_path)
    (input_dir / "task.json").write_text(
        json.dumps(
            {
                "code": "result = 'x' * 1_048_577",
                "max_output_bytes": 10_000_000,
            }
        ),
        encoding="utf-8",
    )

    assert entrypoint.main() == 1
    assert json.loads((output_dir / "error.json").read_text()) == {
        "error": "output_too_large",
        "message": "output_too_large",
    }
    assert not (output_dir / "result.json").exists()


def test_main_reports_syntax_error_without_source(monkeypatch, tmp_path: Path):
    input_dir, output_dir, _ = _configure_main_paths(monkeypatch, tmp_path)
    secret_source = "TOKEN-secret-source ="
    (input_dir / "task.json").write_text(
        json.dumps({"code": secret_source}),
        encoding="utf-8",
    )

    assert entrypoint.main() == 1
    error = json.loads((output_dir / "error.json").read_text())
    assert error == {
        "error": "syntax_error",
        "message": "syntax_error",
        "line": 1,
    }
    assert secret_source not in json.dumps(error)


def test_generated_exception_type_is_allowlisted():
    error = entrypoint._error_payload(
        entrypoint.GeneratedCodeError("SecretTokenError")
    )

    assert error["exception_type"] == "Exception"
    assert "SecretTokenError" not in json.dumps(error)
