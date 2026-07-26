import json

import httpx

from app.advisor.agent.data_agent.sandbox import SandboxClient
from app.advisor.agent.progress import bind_progress_sink
from app.advisor.agent.python_runtime import build_agent_python_tools


def _patch_client(monkeypatch, handler):
    monkeypatch.setenv("SANDBOX_URL", "http://sandbox.test")
    monkeypatch.setenv("SANDBOX_TOKEN", "x" * 32)
    monkeypatch.setattr(
        "app.advisor.agent.python_runtime.SandboxClient.from_env",
        lambda: SandboxClient(
            "http://sandbox.test",
            "x" * 32,
            transport=httpx.MockTransport(handler),
        ),
    )


def test_run_python_script_returns_stdout(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["require_result"] is False
        return httpx.Response(
            200,
            json={
                "ok": True,
                "result": None,
                "stdout": "hello\n",
                "stderr": "",
                "metrics": {"elapsed_ms": 1},
            },
        )

    _patch_client(monkeypatch, handler)
    tools = {t.name: t for t in build_agent_python_tools("u1")}
    payload = json.loads(tools["run_python_script"].invoke({"code": "print('hello')"}))
    assert payload["result"] is None
    assert "hello" in payload["stdout"]


def test_run_python_script_returns_structured_result(monkeypatch):
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "ok": True,
                "result": {"n": 2},
                "stdout": "",
                "stderr": "",
                "metrics": {"elapsed_ms": 1},
            },
        )

    _patch_client(monkeypatch, handler)
    tools = {t.name: t for t in build_agent_python_tools("u1")}
    payload = json.loads(
        tools["run_python_script"].invoke({"code": "result={'n': 2}"})
    )
    assert payload["result"] == {"n": 2}


def test_inline_and_registered_datasets_reach_sandbox(monkeypatch):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "ok": True,
                "result": {"ok": True},
                "stdout": "",
                "stderr": "",
                "metrics": {"elapsed_ms": 1},
            },
        )

    _patch_client(monkeypatch, handler)
    tools = {t.name: t for t in build_agent_python_tools("u1")}
    reg = json.loads(
        tools["register_tool_dataset"].invoke(
            {
                "name": "from_tool",
                "tool_result_json": json.dumps([{"a": 1}, {"a": 2}]),
            }
        )
    )
    assert reg["ok"] is True
    payload = json.loads(
        tools["run_python_script"].invoke(
            {
                "code": "result={'ok': True}",
                "dataset_ids_json": json.dumps(["from_tool"]),
                "inline_datasets_json": json.dumps({"inline": [{"b": 3}]}),
            }
        )
    )
    assert payload["result"] == {"ok": True}
    assert captured["datasets"]["from_tool"] == [{"a": 1}, {"a": 2}]
    assert captured["datasets"]["inline"] == [{"b": 3}]


def test_register_rejects_too_many_rows(monkeypatch):
    monkeypatch.setenv("SANDBOX_URL", "http://sandbox.test")
    monkeypatch.setenv("SANDBOX_TOKEN", "x" * 32)
    tools = {t.name: t for t in build_agent_python_tools("u1")}
    rows = [{"i": i} for i in range(201)]
    payload = json.loads(
        tools["register_tool_dataset"].invoke(
            {"name": "big", "tool_result_json": json.dumps(rows)}
        )
    )
    assert payload["error"]["code"] == "dataset_too_large"


def test_python_call_limit(monkeypatch):
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "ok": True,
                "result": None,
                "stdout": "x",
                "stderr": "",
                "metrics": {"elapsed_ms": 1},
            },
        )

    _patch_client(monkeypatch, handler)
    tools = {t.name: t for t in build_agent_python_tools("u1")}
    for _ in range(3):
        assert "error" not in json.loads(
            tools["run_python_script"].invoke({"code": "print('x')"})
        )
    payload = json.loads(tools["run_python_script"].invoke({"code": "print('x')"}))
    assert payload["error"]["code"] == "python_call_limit_exceeded"


def test_missing_sandbox_config(monkeypatch):
    monkeypatch.delenv("SANDBOX_URL", raising=False)
    monkeypatch.delenv("SANDBOX_TOKEN", raising=False)
    tools = {t.name: t for t in build_agent_python_tools("u1")}
    payload = json.loads(tools["run_python_script"].invoke({"code": "print('x')"}))
    assert payload["error"]["code"] == "sandbox_config_missing"


def test_run_python_emits_main_agent_progress(monkeypatch):
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "ok": True,
                "result": None,
                "stdout": "ok",
                "stderr": "",
                "metrics": {"elapsed_ms": 1},
            },
        )

    _patch_client(monkeypatch, handler)
    events = []
    tools = {t.name: t for t in build_agent_python_tools("u1")}
    with bind_progress_sink(events.append):
        json.loads(tools["run_python_script"].invoke({"code": "print('ok')"}))
    assert [(e["phase"], e["step"], e["status"]) for e in events] == [
        ("main_agent", "run_python", "started"),
        ("main_agent", "run_python", "completed"),
    ]
