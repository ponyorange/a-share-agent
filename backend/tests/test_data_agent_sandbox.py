import json

import httpx
import pytest

from app.advisor.agent.data_agent.models import DataAgentLimits
from app.advisor.agent.data_agent.sandbox import SandboxClient, build_python_tool
from app.advisor.agent.data_agent.workspace import DatasetWorkspace


def test_sandbox_client_sends_token_and_returns_result():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-Sandbox-Token"] == "test-sandbox-token"
        assert request.url.path == "/v1/execute"
        body = json.loads(request.content)
        assert body["timeout_seconds"] == 30
        assert body["memory_mb"] == 512
        assert body["max_output_bytes"] == 1024 * 1024
        return httpx.Response(
            200,
            json={
                "ok": True,
                "result": {"mean": 2.0},
                "metrics": {"elapsed_ms": 4, "memory_peak_mb": 12, "token": "drop"},
            },
        )

    client = SandboxClient(
        base_url="http://sandbox",
        token="test-sandbox-token",
        transport=httpx.MockTransport(handler),
    )
    result = client.execute("result={'mean': 2.0}", {"a": [{"x": 2}]}, DataAgentLimits())
    assert result == {"mean": 2.0}
    assert client.last_metrics == {"elapsed_ms": 4, "memory_peak_mb": 12}


def test_sandbox_client_maps_timeout():
    def handler(_request):
        raise httpx.ReadTimeout("late")

    client = SandboxClient("http://sandbox", "token", transport=httpx.MockTransport(handler))
    with pytest.raises(RuntimeError, match="^sandbox_timeout$"):
        client.execute("result={}", {}, DataAgentLimits())


def test_sandbox_client_maps_controller_execution_timeout_string_error():
    def handler(_request):
        return httpx.Response(
            200,
            json={"ok": False, "error": "execution_timeout", "metrics": {"elapsed_ms": 30_000}},
        )

    client = SandboxClient("http://sandbox", "token", transport=httpx.MockTransport(handler))
    with pytest.raises(RuntimeError, match="^sandbox_timeout$") as exc_info:
        client.execute("secret_code()", {"a": [{"secret": "row"}]}, DataAgentLimits())

    assert exc_info.value.__cause__ is None
    encoded = str(exc_info.value)
    assert "secret_code" not in encoded
    assert "row" not in encoded


def test_sandbox_client_maps_connect_timeout():
    def handler(_request):
        raise httpx.ConnectTimeout("late")

    client = SandboxClient("http://sandbox", "token", transport=httpx.MockTransport(handler))
    with pytest.raises(RuntimeError, match="^sandbox_timeout$"):
        client.execute("result={}", {}, DataAgentLimits())


def test_sandbox_client_rejects_oversized_body_before_json_parse():
    def handler(_request):
        return httpx.Response(
            200,
            content=b'{"ok":true,"result":"' + (b"x" * 20_000) + b'"}',
        )

    client = SandboxClient("http://sandbox", "token", transport=httpx.MockTransport(handler))
    with pytest.raises(RuntimeError, match="^sandbox_invalid_output$"):
        client.execute("result={}", {}, DataAgentLimits(max_output_bytes=1024))


@pytest.mark.parametrize(
    "content",
    [
        b'{"ok": true, "result": {"bad": Infinity}}',
        b'{"ok": true, "result": {"bad": NaN}}',
        b'{"ok": true, "result": [[[[[[[[[[[[[[[[[[[[[["too deep"]]]]]]]]]]]]]]]]]]]]]]}',
    ],
)
def test_sandbox_client_rejects_invalid_json_values(content):
    def handler(_request):
        return httpx.Response(200, content=content)

    client = SandboxClient("http://sandbox", "token", transport=httpx.MockTransport(handler))
    with pytest.raises(RuntimeError, match="^sandbox_invalid_output$"):
        client.execute("result={}", {}, DataAgentLimits())


def test_sandbox_client_rejects_non_string_keys_after_parse():
    class NonStringKeyTransport(httpx.BaseTransport):
        def handle_request(self, request):
            response = httpx.Response(200, content=b'{"ok": true, "result": {}}')
            response.json = lambda: {"ok": True, "result": {1: "x"}}
            return response

    client = SandboxClient("http://sandbox", "token", transport=NonStringKeyTransport())
    with pytest.raises(RuntimeError, match="^sandbox_invalid_output$"):
        client.execute("result={}", {}, DataAgentLimits())


def test_sandbox_client_maps_controller_string_runner_rejection_code():
    def handler(_request):
        return httpx.Response(
            200,
            json={"ok": False, "error": "invalid_dataset_name", "metrics": {"elapsed_ms": 1}},
        )

    client = SandboxClient("http://sandbox", "token", transport=httpx.MockTransport(handler))
    with pytest.raises(RuntimeError, match="^sandbox_rejected:invalid_dataset_name$"):
        client.execute("result={}", {}, DataAgentLimits())


def test_sandbox_client_maps_http_error_status_with_string_error():
    def handler(_request):
        return httpx.Response(400, json={"ok": False, "error": "invalid_request"})

    client = SandboxClient("http://sandbox", "token", transport=httpx.MockTransport(handler))
    with pytest.raises(RuntimeError, match="^sandbox_rejected:invalid_request$"):
        client.execute("result={}", {}, DataAgentLimits())


def test_sandbox_client_runtime_errors_drop_original_exception_chain():
    secret_token = "token-" + ("x" * 40)
    secret_code = "print('code-secret')"
    secret_data = {"a": [{"secret": "data-secret"}]}

    def handler(request):
        raise httpx.ConnectError(
            f"{request.headers['X-Sandbox-Token']} {secret_code} {secret_data}",
            request=request,
        )

    client = SandboxClient("http://sandbox", secret_token, transport=httpx.MockTransport(handler))
    with pytest.raises(RuntimeError, match="^sandbox_unavailable$") as exc_info:
        client.execute(secret_code, secret_data, DataAgentLimits())

    assert exc_info.value.__cause__ is None
    encoded = str(exc_info.value)
    assert secret_token not in encoded
    assert "code-secret" not in encoded
    assert "data-secret" not in encoded


def test_sandbox_client_invalid_json_drops_original_exception_chain():
    def handler(_request):
        return httpx.Response(200, content=b"{not-json")

    client = SandboxClient("http://sandbox", "token", transport=httpx.MockTransport(handler))
    with pytest.raises(RuntimeError, match="^sandbox_invalid_output$") as exc_info:
        client.execute("secret_code()", {"a": [{"secret": "data-secret"}]}, DataAgentLimits())

    assert exc_info.value.__cause__ is None
    encoded = str(exc_info.value)
    assert "secret_code" not in encoded
    assert "data-secret" not in encoded


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ({"SANDBOX_TOKEN": "x" * 32}, "sandbox_config_missing"),
        ({"SANDBOX_URL": "http://sandbox.test"}, "sandbox_config_missing"),
        (
            {"SANDBOX_URL": "http://sandbox.test", "SANDBOX_TOKEN": "short"},
            "sandbox_config_invalid",
        ),
        (
            {"SANDBOX_URL": "ftp://sandbox.test", "SANDBOX_TOKEN": "x" * 32},
            "sandbox_config_invalid",
        ),
        (
            {"SANDBOX_URL": "http:///missing-host", "SANDBOX_TOKEN": "x" * 32},
            "sandbox_config_invalid",
        ),
    ],
)
def test_sandbox_client_from_env_fails_closed(monkeypatch, env, expected):
    monkeypatch.delenv("SANDBOX_URL", raising=False)
    monkeypatch.delenv("SANDBOX_TOKEN", raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    with pytest.raises(RuntimeError, match=f"^{expected}$") as exc_info:
        SandboxClient.from_env()

    assert exc_info.value.__cause__ is None


def test_sandbox_client_from_env_accepts_valid_http_values(monkeypatch):
    monkeypatch.setenv("SANDBOX_URL", "https://sandbox.test")
    monkeypatch.setenv("SANDBOX_TOKEN", "x" * 32)

    client = SandboxClient.from_env()

    assert client.last_metrics == {}


def test_python_analysis_tool_exports_only_requested_workspace_datasets(tmp_path):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={"ok": True, "result": {"mean": 2.0}})

    with DatasetWorkspace(DataAgentLimits(), root=tmp_path / "r") as workspace:
        meta = workspace.create_dataset(
            "akshare",
            "demo",
            {},
            {
                "columns": ["x"],
                "rows": [{"x": 1}, {"x": 3}],
                "returned": 2,
                "total": 2,
                "truncated": False,
            },
        )
        client = SandboxClient("http://sandbox", "token", transport=httpx.MockTransport(handler))
        tool = build_python_tool(workspace, client)
        payload = json.loads(
            tool.invoke(
                {
                    "code": "result={'mean': 2.0}",
                    "dataset_ids_json": json.dumps([meta.dataset_id]),
                }
            )
        )

    assert payload["result"] == {"mean": 2.0}
    assert payload["result_id"]
    assert payload["result_summary"] == {"type": "object", "bytes": 12}
    assert captured["datasets"] == {meta.dataset_id: [{"x": 1}, {"x": 3}]}
    assert "result={'mean': 2.0}" not in json.dumps(payload, ensure_ascii=False)
    assert [{"x": 1}, {"x": 3}] != payload.get("result")


def test_python_analysis_tool_rejects_third_call_without_executing_client(tmp_path):
    class CountingClient:
        def __init__(self):
            self.execute_calls = 0

        def execute(self, code, datasets, limits):
            self.execute_calls += 1
            return {"call": self.execute_calls}

    with DatasetWorkspace(DataAgentLimits(max_python_retries=2), root=tmp_path / "r") as workspace:
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
        client = CountingClient()
        tool = build_python_tool(workspace, client)
        arguments = {
            "code": "result={'ok': True}",
            "dataset_ids_json": json.dumps([meta.dataset_id]),
        }

        first = json.loads(tool.invoke(arguments))
        second = json.loads(tool.invoke(arguments))
        assert first["result"] == {"call": 1}
        assert second["result"] == {"call": 2}
        assert first["result_id"] != second["result_id"]
        assert json.loads(tool.invoke(arguments)) == {
            "error": {
                "code": "python_retry_limit_exceeded",
                "message": "Python 分析重试次数已达上限",
            }
        }

    assert client.execute_calls == 2


@pytest.mark.parametrize(
    "dataset_ids_json",
    [
        "[]",
        "{}",
        '["a", "a"]',
        '["a", 1]',
        "{not-json",
    ],
)
def test_python_analysis_tool_rejects_invalid_dataset_ids(dataset_ids_json):
    client = SandboxClient("http://sandbox", "token", transport=httpx.MockTransport(lambda r: None))
    with DatasetWorkspace(DataAgentLimits()) as workspace:
        tool = build_python_tool(workspace, client)
        payload = json.loads(tool.invoke({"code": "secret_code()", "dataset_ids_json": dataset_ids_json}))

    assert payload == {"error": {"code": "invalid_dataset_ids", "message": "数据集参数错误"}}
    assert "secret_code" not in json.dumps(payload, ensure_ascii=False)


def test_python_analysis_tool_rejects_dataset_from_another_workspace(tmp_path):
    client = SandboxClient("http://sandbox", "token", transport=httpx.MockTransport(lambda r: None))
    with DatasetWorkspace(DataAgentLimits(), root=tmp_path / "r") as workspace:
        tool = build_python_tool(workspace, client)
        payload = json.loads(
            tool.invoke({"code": "result={}", "dataset_ids_json": json.dumps(["foreign"])})
        )

    assert payload == {"error": {"code": "dataset_not_in_request", "message": "数据集不可用"}}


def test_python_analysis_tool_maps_sandbox_timeout_without_leaking_code_or_data(tmp_path):
    def handler(_request):
        raise httpx.ReadTimeout("late")

    with DatasetWorkspace(DataAgentLimits(), root=tmp_path / "r") as workspace:
        meta = workspace.create_dataset(
            "akshare",
            "demo",
            {},
            {
                "columns": ["secret"],
                "rows": [{"secret": "do-not-leak"}],
                "returned": 1,
                "total": 1,
                "truncated": False,
            },
        )
        client = SandboxClient("http://sandbox", "token", transport=httpx.MockTransport(handler))
        tool = build_python_tool(workspace, client)
        payload = json.loads(
            tool.invoke(
                {
                    "code": "raise Exception('code-secret')",
                    "dataset_ids_json": json.dumps([meta.dataset_id]),
                }
            )
        )

    encoded = json.dumps(payload, ensure_ascii=False)
    assert payload == {"error": {"code": "sandbox_timeout", "message": "计算超时"}}
    assert "code-secret" not in encoded
    assert "do-not-leak" not in encoded
