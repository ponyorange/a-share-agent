"""HTTP controller for one-shot, policy-constrained sandbox containers."""

from __future__ import annotations

import asyncio
import io
import json
import os
import re
import secrets
import tarfile
import threading
import time
from typing import Any

import docker
import requests
from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from starlette.responses import JSONResponse


MAX_INPUT_BYTES = 50 * 1024 * 1024
MAX_TIMEOUT_SECONDS = 30
MAX_MEMORY_MB = 512
MAX_OUTPUT_BYTES = 1024 * 1024
MAX_ARCHIVE_BYTES = 2 * 1024 * 1024
MAX_EMPTY_BODY_CHUNKS = 32
BODY_READ_TIMEOUT_SECONDS = 10
CLEANUP_BUFFER_SECONDS = 5
MIN_SANDBOX_TOKEN_BYTES = 32
DATASET_NAME = re.compile(r"^[A-Za-z0-9_-]+$")
SAFE_RUNNER_ERRORS = {
    "generated_code_failed",
    "import_not_allowed",
    "invalid_output_limit",
    "output_too_large",
    "result_not_assigned",
    "result_not_finite",
    "runner_failed",
    "syntax_error",
}


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True)

    runner_image: str

    @classmethod
    def from_environment(cls) -> "Settings":
        return cls(runner_image=os.environ["SANDBOX_RUNNER_IMAGE"])


class ExecuteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    datasets: dict[str, list[dict[str, Any]]]
    timeout_seconds: int = Field(gt=0)
    memory_mb: int = Field(gt=0)
    max_output_bytes: int = Field(gt=0)


class ExecuteResponse(BaseModel):
    ok: bool
    result: Any | None = None
    error: str | None = None
    metrics: dict[str, int]


class HealthResponse(BaseModel):
    docker_reachable: bool
    runner_image_available: bool


def sandbox_token_from_environment() -> str:
    token = os.environ["SANDBOX_TOKEN"].strip()
    if len(token.encode("utf-8")) < MIN_SANDBOX_TOKEN_BYTES:
        raise RuntimeError("sandbox_token_too_short")
    return token


class RawBodyLimitMiddleware:
    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if (
            scope["type"] != "http"
            or scope.get("method") != "POST"
            or scope.get("path") != "/v1/execute"
        ):
            await self.app(scope, receive, send)
            return

        for name, value in scope.get("headers", []):
            if name.lower() != b"content-length":
                continue
            try:
                if int(value) > MAX_INPUT_BYTES:
                    await self._reject(
                        scope,
                        receive,
                        send,
                        status_code=413,
                        detail="input_too_large",
                    )
                    return
            except (TypeError, ValueError):
                pass

        body = bytearray()
        empty_chunks = 0
        disconnected = False
        try:
            async with asyncio.timeout(BODY_READ_TIMEOUT_SECONDS):
                while True:
                    message = await receive()
                    if message["type"] == "http.disconnect":
                        disconnected = True
                        break
                    if message["type"] != "http.request":
                        continue
                    chunk = message.get("body", b"")
                    if chunk:
                        if len(body) + len(chunk) > MAX_INPUT_BYTES:
                            await self._reject(
                                scope,
                                receive,
                                send,
                                status_code=413,
                                detail="input_too_large",
                            )
                            return
                        body.extend(chunk)
                    else:
                        empty_chunks += 1
                        if empty_chunks > MAX_EMPTY_BODY_CHUNKS:
                            await self._reject(
                                scope,
                                receive,
                                send,
                                status_code=408,
                                detail="request_timeout",
                            )
                            return
                    if not message.get("more_body", False):
                        break
        except TimeoutError:
            await self._reject(
                scope,
                receive,
                send,
                status_code=408,
                detail="request_timeout",
            )
            return

        if disconnected:
            await self._reject(
                scope,
                receive,
                send,
                status_code=400,
                detail="client_disconnected",
            )
            return

        normalized_body = bytes(body)
        del body
        replayed = False

        async def replay_receive() -> dict[str, Any]:
            nonlocal replayed
            if replayed:
                return await receive()
            replayed = True
            return {
                "type": "http.request",
                "body": normalized_body,
                "more_body": False,
            }

        await self.app(scope, replay_receive, send)

    @staticmethod
    async def _reject(
        scope: dict[str, Any],
        receive: Any,
        send: Any,
        *,
        status_code: int,
        detail: str,
    ) -> None:
        response = JSONResponse(
            status_code=status_code,
            content={"detail": detail},
        )
        await response(scope, receive, send)


def serialized_request_size(request: ExecuteRequest) -> int:
    return len(request.model_dump_json().encode("utf-8"))


def _json_tar(entries: list[tuple[str, Any]]) -> bytes:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as archive:
        for name, value in entries:
            encoded = json.dumps(
                value,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            ).encode("utf-8")
            info = tarfile.TarInfo(name)
            info.size = len(encoded)
            info.mode = 0o400
            info.uid = 65532
            info.gid = 65532
            archive.addfile(info, io.BytesIO(encoded))
    return output.getvalue()


def _read_json_archive(
    chunks: Any,
    *,
    expected_name: str,
    max_bytes: int = MAX_OUTPUT_BYTES,
    archive_size: Any = None,
) -> Any:
    if isinstance(archive_size, int) and archive_size > MAX_ARCHIVE_BYTES:
        raise OverflowError("output_too_large")
    raw_archive = io.BytesIO()
    received_bytes = 0
    for chunk in chunks:
        received_bytes += len(chunk)
        if received_bytes > MAX_ARCHIVE_BYTES:
            raise OverflowError("output_too_large")
        raw_archive.write(chunk)
    raw_archive.seek(0)
    with tarfile.open(fileobj=raw_archive, mode="r:*") as archive:
        members = archive.getmembers()
        if len(members) != 1:
            raise ValueError("invalid_output_archive")
        member = members[0]
        if member.name != expected_name or not member.isfile():
            raise ValueError("invalid_output_archive")
        if member.size > max_bytes:
            raise OverflowError("output_too_large")
        source = archive.extractfile(member)
        if source is None:
            raise ValueError("invalid_output_archive")
        encoded = source.read(max_bytes + 1)
        if len(encoded) > max_bytes:
            raise OverflowError("output_too_large")
    return json.loads(encoded)


def _effective_limits(request: ExecuteRequest) -> tuple[int, int, int]:
    return (
        min(request.timeout_seconds, MAX_TIMEOUT_SECONDS),
        min(request.memory_mb, MAX_MEMORY_MB),
        min(request.max_output_bytes, MAX_OUTPUT_BYTES),
    )


class DockerExecutor:
    def __init__(self, docker_client: Any, settings: Settings) -> None:
        self._docker = docker_client
        self._settings = settings

    def execute(self, request: ExecuteRequest) -> dict[str, Any]:
        started_at = time.monotonic()
        container = None

        def response(ok: bool, *, result: Any = None, error: str | None = None):
            payload: dict[str, Any] = {
                "ok": ok,
                "metrics": {
                    "elapsed_ms": max(
                        0,
                        int((time.monotonic() - started_at) * 1000),
                    )
                },
            }
            if ok:
                payload["result"] = result
            else:
                payload["error"] = error or "sandbox_failed"
            return payload

        if any(not DATASET_NAME.fullmatch(name) for name in request.datasets):
            return response(False, error="invalid_dataset_name")
        if serialized_request_size(request) > MAX_INPUT_BYTES:
            return response(False, error="input_too_large")

        timeout_seconds, memory_mb, max_output_bytes = _effective_limits(request)
        entries: list[tuple[str, Any]] = [
            (f"datasets/{name}.json", rows)
            for name, rows in sorted(request.datasets.items())
        ]
        entries.append(
            (
                "task.json",
                {
                    "code": request.code,
                    "max_output_bytes": max_output_bytes,
                },
            )
        )

        try:
            input_archive = _json_tar(entries)
            container = self._docker.containers.create(
                image=self._settings.runner_image,
                entrypoint=["sh", "-c"],
                command=[
                    "while [ ! -f /input/task.json ]; do sleep 0.05; done; "
                    "python /runner/entrypoint.py"
                ],
                network_disabled=True,
                read_only=True,
                user="65532:65532",
                cap_drop=["ALL"],
                security_opt=["no-new-privileges:true"],
                mem_limit=f"{memory_mb}m",
                nano_cpus=1_000_000_000,
                pids_limit=32,
                tmpfs={
                    "/input": "rw,noexec,nosuid,size=52m,uid=65532,gid=65532",
                    "/output": "rw,noexec,nosuid,size=2m,uid=65532,gid=65532",
                    "/tmp": "rw,noexec,nosuid,size=64m,uid=65532,gid=65532",
                },
                labels={"share-data.sandbox": "ephemeral"},
                detach=True,
            )
            container.start()
            if not container.put_archive("/input", input_archive):
                raise RuntimeError("archive_rejected")
            deadline_reached = threading.Event()

            def enforce_deadline() -> None:
                deadline_reached.set()
                try:
                    container.kill()
                except Exception:
                    pass

            deadline = threading.Timer(timeout_seconds, enforce_deadline)
            deadline.daemon = True
            deadline.start()
            try:
                try:
                    status = container.wait(
                        timeout=timeout_seconds + CLEANUP_BUFFER_SECONDS
                    )
                finally:
                    deadline.cancel()
            except (TimeoutError, requests.exceptions.Timeout):
                try:
                    container.kill()
                except Exception:
                    pass
                return response(False, error="execution_timeout")
            if deadline_reached.is_set():
                return response(False, error="execution_timeout")

            if int(status.get("StatusCode", 1)) == 0:
                stream, stat = container.get_archive("/output/result.json")
                result = _read_json_archive(
                    stream,
                    expected_name="result.json",
                    archive_size=stat.get("size"),
                )
                return response(True, result=result)

            stream, stat = container.get_archive("/output/error.json")
            error_payload = _read_json_archive(
                stream,
                expected_name="error.json",
                archive_size=stat.get("size"),
            )
            error_code = (
                error_payload.get("error")
                if isinstance(error_payload, dict)
                else None
            )
            if error_code not in SAFE_RUNNER_ERRORS:
                error_code = "runner_failed"
            return response(False, error=error_code)
        except OverflowError:
            return response(False, error="output_too_large")
        except Exception:
            return response(False, error="sandbox_failed")
        finally:
            if container is not None:
                try:
                    container.remove(force=True)
                except Exception:
                    pass


def get_docker_client():
    return docker.from_env()


def get_executor(
    docker_client: Any = Depends(get_docker_client),
) -> DockerExecutor:
    return DockerExecutor(docker_client, Settings.from_environment())


app = FastAPI()
app.add_middleware(RawBodyLimitMiddleware)


@app.post("/v1/execute", response_model=ExecuteResponse)
def execute(
    body: ExecuteRequest,
    x_sandbox_token: str = Header(default=""),
    executor: DockerExecutor = Depends(get_executor),
) -> ExecuteResponse:
    try:
        expected = sandbox_token_from_environment()
    except (KeyError, RuntimeError):
        raise HTTPException(status_code=503, detail="sandbox_misconfigured") from None
    if not secrets.compare_digest(x_sandbox_token, expected):
        raise HTTPException(status_code=401, detail="unauthorized")
    if serialized_request_size(body) > MAX_INPUT_BYTES:
        raise HTTPException(status_code=413, detail="input_too_large")
    return ExecuteResponse.model_validate(executor.execute(body))


@app.get("/health", response_model=HealthResponse)
def health(
    docker_client: Any = Depends(get_docker_client),
) -> HealthResponse:
    docker_reachable = False
    runner_image_available = False
    try:
        docker_reachable = bool(docker_client.ping())
        if docker_reachable:
            docker_client.images.get(Settings.from_environment().runner_image)
            runner_image_available = True
    except Exception:
        pass
    return HealthResponse(
        docker_reachable=docker_reachable,
        runner_image_available=runner_image_available,
    )
