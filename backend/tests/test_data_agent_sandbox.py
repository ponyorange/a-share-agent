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


def test_sandbox_client_maps_controller_rejection_code():
    def handler(_request):
        return httpx.Response(
            400,
            json={"ok": False, "error": {"code": "invalid_request", "message": "bad"}},
        )

    client = SandboxClient("http://sandbox", "token", transport=httpx.MockTransport(handler))
    with pytest.raises(RuntimeError, match="^sandbox_rejected:invalid_request$"):
        client.execute("result={}", {}, DataAgentLimits())


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

    assert payload == {"result": {"mean": 2.0}}
    assert captured["datasets"] == {meta.dataset_id: [{"x": 1}, {"x": 3}]}
    assert "result={'mean': 2.0}" not in json.dumps(payload, ensure_ascii=False)
    assert [{"x": 1}, {"x": 3}] != payload.get("result")


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
