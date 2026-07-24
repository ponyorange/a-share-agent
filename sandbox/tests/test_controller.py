import asyncio
import io
import json
import tarfile
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from controller import app as controller


TOKEN = "test-token-with-at-least-32-bytes"
REQUEST = {
    "code": "result = {'sum': 3}",
    "datasets": {"prices": [{"close": 3}]},
    "timeout_seconds": 30,
    "memory_mb": 512,
    "max_output_bytes": 1_048_576,
}


class FakeExecutor:
    def __init__(self):
        self.calls = 0

    def execute(self, request):
        self.calls += 1
        return {"ok": True, "result": {"sum": 3}, "metrics": {"elapsed_ms": 5}}


class FakeContainer:
    def __init__(
        self,
        *,
        wait_result=None,
        archives=None,
        archive_stats=None,
        wait_error=None,
    ):
        self.wait_result = wait_result or {"StatusCode": 0}
        self.archives = archives or {}
        self.archive_stats = archive_stats or {}
        self.wait_error = wait_error
        self.started = False
        self.killed = False
        self.removed = False
        self.put_calls = []
        self.wait_timeout = None

    def start(self):
        self.started = True

    def put_archive(self, path, data):
        self.put_calls.append((path, data))
        return True

    def wait(self, timeout):
        self.wait_timeout = timeout
        if self.wait_error:
            raise self.wait_error
        return self.wait_result

    def kill(self):
        self.killed = True

    def get_archive(self, path):
        archive = self.archives[path]
        chunks = archive if hasattr(archive, "__next__") else iter([archive])
        return chunks, self.archive_stats.get(path, {})

    def remove(self, force=False):
        assert force is True
        self.removed = True


class FakeDockerClient:
    def __init__(self, container):
        self.container = container
        self.create_kwargs = None
        self.containers = SimpleNamespace(create=self.create)

    def create(self, **kwargs):
        self.create_kwargs = kwargs
        return self.container


@pytest.fixture(autouse=True)
def clean_overrides(monkeypatch):
    monkeypatch.setenv("SANDBOX_TOKEN", TOKEN)
    monkeypatch.setenv("SANDBOX_RUNNER_IMAGE", "fixed-runner:test")
    controller.app.dependency_overrides.clear()
    controller.app.dependency_overrides[controller.get_executor] = lambda: FakeExecutor()
    yield
    controller.app.dependency_overrides.clear()


def _archive(name, payload):
    output = io.BytesIO()
    encoded = json.dumps(payload).encode()
    with tarfile.open(fileobj=output, mode="w") as archive:
        info = tarfile.TarInfo(name)
        info.size = len(encoded)
        archive.addfile(info, io.BytesIO(encoded))
    return output.getvalue()


def _executor(container):
    client = FakeDockerClient(container)
    settings = controller.Settings(runner_image="fixed-runner:test")
    return controller.DockerExecutor(client, settings), client


def _asgi_post(
    messages=None,
    *,
    content_length=None,
    receive_override=None,
    app=None,
):
    sent = []
    headers = [
        (b"content-type", b"application/json"),
        (b"x-sandbox-token", TOKEN.encode()),
    ]
    if content_length is not None:
        headers.append((b"content-length", str(content_length).encode()))
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/v1/execute",
        "raw_path": b"/v1/execute",
        "query_string": b"",
        "headers": headers,
        "client": ("test", 123),
        "server": ("testserver", 80),
    }
    pending = iter(messages or [])

    async def receive():
        return next(pending, {"type": "http.disconnect"})

    async def send(message):
        sent.append(message)

    async def call_app():
        await (app or controller.app)(
            scope,
            receive_override or receive,
            send,
        )

    asyncio.run(asyncio.wait_for(call_app(), timeout=0.5))
    status = next(message["status"] for message in sent if message["type"] == "http.response.start")
    body = b"".join(
        message.get("body", b"")
        for message in sent
        if message["type"] == "http.response.body"
    )
    return status, json.loads(body) if body else None


def test_execute_requires_token():
    controller.app.dependency_overrides[controller.get_executor] = lambda: FakeExecutor()
    client = TestClient(controller.app)

    assert client.post("/v1/execute", json=REQUEST).status_code == 401
    response = client.post(
        "/v1/execute",
        json=REQUEST,
        headers={"X-Sandbox-Token": TOKEN},
    )

    assert response.status_code == 200
    assert response.json()["result"]["sum"] == 3


def test_execute_fails_closed_when_configured_token_is_too_short(monkeypatch):
    monkeypatch.setenv("SANDBOX_TOKEN", "short")
    controller.app.dependency_overrides[controller.get_executor] = lambda: FakeExecutor()

    response = TestClient(controller.app).post(
        "/v1/execute",
        json=REQUEST,
        headers={"X-Sandbox-Token": "short"},
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "sandbox_misconfigured"


def test_execute_authentication_uses_compare_digest(monkeypatch):
    calls = []
    monkeypatch.setattr(
        controller.secrets,
        "compare_digest",
        lambda supplied, expected: calls.append((supplied, expected)) or True,
    )
    controller.app.dependency_overrides[controller.get_executor] = lambda: FakeExecutor()

    response = TestClient(controller.app).post(
        "/v1/execute",
        json=REQUEST,
        headers={"X-Sandbox-Token": "supplied"},
    )

    assert response.status_code == 200
    assert calls == [("supplied", TOKEN)]


@pytest.mark.parametrize("field", ["image", "command", "mounts", "network"])
def test_execute_rejects_client_controlled_container_options(field):
    body = {**REQUEST, field: "attacker-controlled"}
    response = TestClient(controller.app).post(
        "/v1/execute",
        json=body,
        headers={"X-Sandbox-Token": TOKEN},
    )

    assert response.status_code == 422


def test_raw_body_limit_rejects_large_json_whitespace_before_executor(monkeypatch):
    monkeypatch.setattr(controller, "MAX_INPUT_BYTES", 128)
    monkeypatch.setattr(controller, "serialized_request_size", lambda request: 1)
    executor = FakeExecutor()
    controller.app.dependency_overrides[controller.get_executor] = lambda: executor
    raw_body = (b" " * 129) + json.dumps(REQUEST).encode()

    response = TestClient(controller.app).post(
        "/v1/execute",
        content=raw_body,
        headers={"Content-Type": "application/json", "X-Sandbox-Token": TOKEN},
    )

    assert response.status_code == 413
    assert response.json()["detail"] == "input_too_large"
    assert executor.calls == 0


def test_raw_body_limit_rejects_oversized_content_length_without_receiving(monkeypatch):
    monkeypatch.setattr(controller, "MAX_INPUT_BYTES", 128)
    executor = FakeExecutor()
    controller.app.dependency_overrides[controller.get_executor] = lambda: executor

    status, payload = _asgi_post([], content_length=129)

    assert status == 413
    assert payload["detail"] == "input_too_large"
    assert executor.calls == 0


def test_raw_body_limit_counts_chunks_despite_forged_small_content_length(monkeypatch):
    raw_body = json.dumps(REQUEST).encode()
    monkeypatch.setattr(controller, "MAX_INPUT_BYTES", len(raw_body) - 1)
    executor = FakeExecutor()
    controller.app.dependency_overrides[controller.get_executor] = lambda: executor
    split = len(raw_body) // 2

    status, payload = _asgi_post(
        [
            {"type": "http.request", "body": raw_body[:split], "more_body": True},
            {"type": "http.request", "body": raw_body[split:], "more_body": False},
        ],
        content_length=1,
    )

    assert status == 413
    assert payload["detail"] == "input_too_large"
    assert executor.calls == 0


def test_raw_body_limit_allows_streaming_client_without_content_length(monkeypatch):
    raw_body = json.dumps(REQUEST).encode()
    monkeypatch.setattr(controller, "MAX_INPUT_BYTES", len(raw_body))
    executor = FakeExecutor()
    controller.app.dependency_overrides[controller.get_executor] = lambda: executor
    split = len(raw_body) // 2

    status, payload = _asgi_post(
        [
            {"type": "http.request", "body": raw_body[:split], "more_body": True},
            {"type": "http.request", "body": raw_body[split:], "more_body": False},
        ]
    )

    assert status == 200
    assert payload["ok"] is True
    assert executor.calls == 1


def test_raw_body_limit_rejects_too_many_empty_chunks_before_executor(monkeypatch):
    monkeypatch.setattr(controller, "MAX_EMPTY_BODY_CHUNKS", 2)
    executor = FakeExecutor()
    controller.app.dependency_overrides[controller.get_executor] = lambda: executor

    status, payload = _asgi_post(
        [
            {"type": "http.request", "body": b"", "more_body": True},
            {"type": "http.request", "body": b"", "more_body": True},
            {"type": "http.request", "body": b"", "more_body": True},
            {
                "type": "http.request",
                "body": json.dumps(REQUEST).encode(),
                "more_body": False,
            },
        ]
    )

    assert status == 408
    assert payload["detail"] == "request_timeout"
    assert executor.calls == 0


def test_raw_body_limit_allows_empty_chunks_before_valid_data(monkeypatch):
    monkeypatch.setattr(controller, "MAX_EMPTY_BODY_CHUNKS", 3)
    executor = FakeExecutor()
    controller.app.dependency_overrides[controller.get_executor] = lambda: executor

    status, payload = _asgi_post(
        [
            {"type": "http.request", "body": b"", "more_body": True},
            {"type": "http.request", "body": b"", "more_body": True},
            {
                "type": "http.request",
                "body": json.dumps(REQUEST).encode(),
                "more_body": False,
            },
        ]
    )

    assert status == 200
    assert payload["ok"] is True
    assert executor.calls == 1


def test_raw_body_limit_handles_legal_empty_terminal_without_executor():
    executor = FakeExecutor()
    controller.app.dependency_overrides[controller.get_executor] = lambda: executor

    status, _ = _asgi_post(
        [{"type": "http.request", "body": b"", "more_body": False}]
    )

    assert status in {400, 422}
    assert executor.calls == 0


def test_raw_body_limit_rejects_disconnect_without_treating_it_as_body():
    executor = FakeExecutor()
    controller.app.dependency_overrides[controller.get_executor] = lambda: executor

    status, payload = _asgi_post([{"type": "http.disconnect"}])

    assert status == 400
    assert payload["detail"] == "client_disconnected"
    assert executor.calls == 0


def test_raw_body_limit_deadline_rejects_slow_request_before_executor(monkeypatch):
    monkeypatch.setattr(controller, "BODY_READ_TIMEOUT_SECONDS", 0.01)
    executor = FakeExecutor()
    controller.app.dependency_overrides[controller.get_executor] = lambda: executor

    async def never_receive():
        await asyncio.sleep(1)
        return {"type": "http.disconnect"}

    status, payload = _asgi_post(receive_override=never_receive)

    assert status == 408
    assert payload["detail"] == "request_timeout"
    assert executor.calls == 0


def test_raw_body_limit_replays_one_normalized_request_message():
    received = []

    async def downstream(scope, receive, send):
        received.append(await receive())
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    middleware = controller.RawBodyLimitMiddleware(downstream)

    status, _ = _asgi_post(
        [
            {"type": "http.request", "body": b'{"a":', "more_body": True},
            {"type": "http.request", "body": b"1}", "more_body": False},
        ],
        app=middleware,
    )

    assert status == 204
    assert received == [
        {"type": "http.request", "body": b'{"a":1}', "more_body": False}
    ]


def test_raw_body_limit_delegates_receive_after_normalized_body():
    received = []
    real_followup = {"type": "http.disconnect", "marker": "fake-server-event"}

    async def downstream(scope, receive, send):
        received.append(await receive())
        received.append(await receive())
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    middleware = controller.RawBodyLimitMiddleware(downstream)

    status, _ = _asgi_post(
        [
            {"type": "http.request", "body": b'{"a":', "more_body": True},
            {"type": "http.request", "body": b"1}", "more_body": False},
            real_followup,
        ],
        app=middleware,
    )

    assert status == 204
    assert received == [
        {"type": "http.request", "body": b'{"a":1}', "more_body": False},
        real_followup,
    ]


def test_execute_rejects_serialized_input_over_50_mib(monkeypatch):
    monkeypatch.setattr(controller, "serialized_request_size", lambda request: 50 * 1024 * 1024 + 1)
    controller.app.dependency_overrides[controller.get_executor] = lambda: FakeExecutor()

    response = TestClient(controller.app).post(
        "/v1/execute",
        json=REQUEST,
        headers={"X-Sandbox-Token": TOKEN},
    )

    assert response.status_code == 413
    assert response.json()["detail"] == "input_too_large"


def test_executor_uses_fixed_security_create_kwargs_and_hard_caps():
    container = FakeContainer(
        archives={"/output/result.json": _archive("result.json", {"sum": 3})}
    )
    executor, client = _executor(container)
    request = controller.ExecuteRequest(
        **{
            **REQUEST,
            "timeout_seconds": 500,
            "memory_mb": 4096,
            "max_output_bytes": 99_000_000,
        }
    )

    response = executor.execute(request)

    assert response["ok"] is True
    assert client.create_kwargs == {
        "image": "fixed-runner:test",
        "entrypoint": ["sh", "-c"],
        "command": [
            "while [ ! -f /input/task.json ]; do sleep 0.05; done; "
            "python /runner/entrypoint.py"
        ],
        "network_disabled": True,
        "read_only": True,
        "user": "65532:65532",
        "cap_drop": ["ALL"],
        "security_opt": ["no-new-privileges:true"],
        "mem_limit": "512m",
        "nano_cpus": 1_000_000_000,
        "pids_limit": 32,
        "tmpfs": {
            "/input": "rw,noexec,nosuid,size=52m,uid=65532,gid=65532",
            "/output": "rw,noexec,nosuid,size=2m,uid=65532,gid=65532",
            "/tmp": "rw,noexec,nosuid,size=64m,uid=65532,gid=65532",
        },
        "labels": {"share-data.sandbox": "ephemeral"},
        "detach": True,
    }
    assert container.wait_timeout == 35
    assert container.removed is True
    assert len(client.create_kwargs["command"]) == 1
    assert "while [ ! -f /input/task.json ]" in client.create_kwargs["command"][0]
    assert "python /runner/entrypoint.py" in client.create_kwargs["command"][0]

    _, archive_bytes = container.put_calls[0]
    with tarfile.open(fileobj=io.BytesIO(archive_bytes)) as archive:
        task = json.load(archive.extractfile("task.json"))
    assert task["max_output_bytes"] == 1_048_576


def test_executor_puts_task_and_datasets_in_safe_archive_paths():
    container = FakeContainer(
        archives={"/output/result.json": _archive("result.json", 3)}
    )
    executor, _ = _executor(container)

    executor.execute(controller.ExecuteRequest(**REQUEST))

    assert container.started is True
    assert container.put_calls[0][0] == "/input"
    with tarfile.open(fileobj=io.BytesIO(container.put_calls[0][1])) as archive:
        assert archive.getnames() == ["datasets/prices.json", "task.json"]
        assert json.load(archive.extractfile("datasets/prices.json")) == [{"close": 3}]


@pytest.mark.parametrize(
    "dataset_name",
    ["../escape", "/absolute", "nested/name", "dot..dot", "", "name.json"],
)
def test_executor_rejects_unsafe_dataset_paths_before_container_create(dataset_name):
    container = FakeContainer()
    executor, client = _executor(container)
    body = {**REQUEST, "datasets": {dataset_name: []}}

    response = executor.execute(controller.ExecuteRequest(**body))

    assert response["ok"] is False
    assert response["error"] == "invalid_dataset_name"
    assert client.create_kwargs is None


def test_executor_kills_timeout_and_always_force_removes():
    container = FakeContainer(wait_error=TimeoutError("private timeout details"))
    executor, _ = _executor(container)

    response = executor.execute(controller.ExecuteRequest(**REQUEST))

    assert response["ok"] is False
    assert response["error"] == "execution_timeout"
    assert container.killed is True
    assert container.removed is True
    assert "private" not in json.dumps(response)


def test_executor_enforces_execution_deadline_before_cleanup_buffer(monkeypatch):
    timers = []

    class FakeTimer:
        def __init__(self, interval, callback):
            self.interval = interval
            self.callback = callback
            self.started = False
            self.cancelled = False
            timers.append(self)

        def start(self):
            self.started = True
            self.callback()

        def cancel(self):
            self.cancelled = True

    monkeypatch.setattr(controller.threading, "Timer", FakeTimer)
    container = FakeContainer(
        archives={"/output/result.json": _archive("result.json", 3)}
    )
    executor, _ = _executor(container)

    response = executor.execute(
        controller.ExecuteRequest(**{**REQUEST, "timeout_seconds": 500})
    )

    assert len(timers) == 1
    assert timers[0].interval == 30
    assert timers[0].started is True
    assert timers[0].cancelled is True
    assert container.killed is True
    assert container.wait_timeout == 35
    assert response["error"] == "execution_timeout"


@pytest.mark.parametrize("failure_stage", ["start", "put_archive", "wait", "get_archive"])
def test_executor_force_removes_on_every_exception(failure_stage, monkeypatch):
    container = FakeContainer(
        archives={"/output/result.json": _archive("result.json", 3)}
    )
    monkeypatch.setattr(
        container,
        failure_stage,
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("SECRET input")),
    )
    executor, _ = _executor(container)

    response = executor.execute(controller.ExecuteRequest(**REQUEST))

    assert response["ok"] is False
    assert response["error"] == "sandbox_failed"
    assert container.removed is True
    assert "SECRET" not in json.dumps(response)


def test_executor_reads_sanitized_error_archive_without_docker_logs():
    error = {
        "error": "generated_code_failed",
        "message": "generated_code_failed",
        "exception_type": "KeyError",
    }
    container = FakeContainer(
        wait_result={"StatusCode": 1},
        archives={"/output/error.json": _archive("error.json", error)},
    )
    container.logs = lambda: b"SECRET docker output"
    executor, _ = _executor(container)

    response = executor.execute(controller.ExecuteRequest(**REQUEST))

    assert response["ok"] is False
    assert response["error"] == "generated_code_failed"
    assert "SECRET" not in json.dumps(response)
    assert container.removed is True


@pytest.mark.parametrize(
    "member_name",
    ["../result.json", "/output/result.json", "nested/result.json"],
)
def test_executor_rejects_unsafe_output_archive_paths(member_name):
    container = FakeContainer(
        archives={"/output/result.json": _archive(member_name, {"secret": "value"})}
    )
    executor, _ = _executor(container)

    response = executor.execute(controller.ExecuteRequest(**REQUEST))

    assert response["ok"] is False
    assert response["error"] == "sandbox_failed"
    assert "secret" not in json.dumps(response)
    assert container.removed is True


def test_executor_rejects_output_over_one_mib():
    container = FakeContainer(
        archives={
            "/output/result.json": _archive(
                "result.json", {"value": "x" * (1_048_576 + 1)}
            )
        }
    )
    executor, _ = _executor(container)

    response = executor.execute(controller.ExecuteRequest(**REQUEST))

    assert response["ok"] is False
    assert response["error"] == "output_too_large"
    assert container.removed is True


def test_output_archive_limit_stops_consuming_chunks_at_two_mib():
    consumed = []

    def oversized_chunks():
        consumed.append(1)
        yield b"x" * controller.MAX_ARCHIVE_BYTES
        consumed.append(2)
        yield b"x"
        raise AssertionError("archive stream was consumed after crossing limit")

    with pytest.raises(OverflowError, match="^output_too_large$"):
        controller._read_json_archive(
            oversized_chunks(),
            expected_name="result.json",
        )

    assert consumed == [1, 2]


def test_executor_rejects_get_archive_stat_size_before_consuming_stream():
    def must_not_read():
        raise AssertionError("oversized archive stream was consumed")
        yield b""

    container = FakeContainer(
        archives={"/output/result.json": must_not_read()},
        archive_stats={
            "/output/result.json": {"size": controller.MAX_ARCHIVE_BYTES + 1}
        },
    )
    executor, _ = _executor(container)

    response = executor.execute(controller.ExecuteRequest(**REQUEST))

    assert response["ok"] is False
    assert response["error"] == "output_too_large"
    assert container.removed is True


def test_health_does_not_leak_daemon_address():
    class HealthClient:
        api = SimpleNamespace(base_url="tcp://secret-daemon.internal:2375")
        images = SimpleNamespace(get=lambda image: object())

        @staticmethod
        def ping():
            return True

    controller.app.dependency_overrides[controller.get_docker_client] = HealthClient

    response = TestClient(controller.app).get("/health")

    assert response.status_code == 200
    assert response.json() == {"docker_reachable": True, "runner_image_available": True}
    assert "secret-daemon" not in response.text
