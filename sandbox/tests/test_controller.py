import io
import json
import tarfile
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from controller import app as controller


TOKEN = "test-token"
REQUEST = {
    "code": "result = {'sum': 3}",
    "datasets": {"prices": [{"close": 3}]},
    "timeout_seconds": 30,
    "memory_mb": 512,
    "max_output_bytes": 1_048_576,
}


class FakeExecutor:
    def execute(self, request):
        return {"ok": True, "result": {"sum": 3}, "metrics": {"elapsed_ms": 5}}


class FakeContainer:
    def __init__(self, *, wait_result=None, archives=None, wait_error=None):
        self.wait_result = wait_result or {"StatusCode": 0}
        self.archives = archives or {}
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
        return iter([self.archives[path]]), {}

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
        "command": (
            "while [ ! -f /input/task.json ]; do sleep 0.05; done; "
            "python /runner/entrypoint.py"
        ),
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
