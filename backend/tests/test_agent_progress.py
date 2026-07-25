import threading

import pytest

from app.advisor.agent.progress import (
    ProgressEvent,
    ProgressValidationError,
    bind_progress_sink,
    emit_progress,
    progress_to_tool_trace,
)


def test_emit_progress_without_sink_is_noop():
    emit_progress(step="fetch", status="started")


def test_bound_sink_receives_only_allowlisted_fields():
    rows = []
    with bind_progress_sink(rows.append):
        emit_progress(
            step="fetch",
            status="completed",
            source="akshare",
            interface="stock_zh_index_daily_tx",
            rows=53,
            truncated=False,
        )
    assert rows == [
        {
            "phase": "data_agent",
            "step": "fetch",
            "status": "completed",
            "message": "已获取 53 行数据",
            "source": "akshare",
            "interface": "stock_zh_index_daily_tx",
            "rows": 53,
            "truncated": False,
        }
    ]


def test_progress_trace_never_contains_parameters_or_data():
    event = ProgressEvent(
        step="fetch",
        status="completed",
        source="akshare",
        interface="daily",
        rows=2,
    )
    trace = progress_to_tool_trace(event.as_dict())
    assert trace == {
        "tool": "data_agent.fetch",
        "content": "akshare/daily：2 行",
    }
    assert "params" not in str(trace)


def test_concurrent_sinks_are_isolated():
    left: list[dict[str, object]] = []
    right: list[dict[str, object]] = []
    barrier = threading.Barrier(2)

    def worker(sink: list[dict[str, object]], source: str) -> None:
        with bind_progress_sink(sink.append):
            barrier.wait()
            emit_progress(
                step="fetch",
                status="completed",
                source=source,
                rows=1,
            )

    t1 = threading.Thread(target=worker, args=(left, "left-source"))
    t2 = threading.Thread(target=worker, args=(right, "right-source"))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert len(left) == 1
    assert left[0]["source"] == "left-source"
    assert len(right) == 1
    assert right[0]["source"] == "right-source"

    orphan: list[dict[str, object]] = []
    with bind_progress_sink(orphan.append):
        pass
    emit_progress(step="fetch", status="started")
    assert orphan == []


@pytest.mark.parametrize("step", ["invalid", "", "FETCH"])
def test_emit_progress_rejects_invalid_step(step: str):
    with pytest.raises(ProgressValidationError):
        emit_progress(step=step, status="started")


@pytest.mark.parametrize("status", ["running", "", "STARTED"])
def test_emit_progress_rejects_invalid_status(status: str):
    with pytest.raises(ProgressValidationError):
        emit_progress(step="fetch", status=status)


@pytest.mark.parametrize(
    "error_code",
    ["ProviderError", "bad-code", "1bad", "x" * 65, ""],
)
def test_emit_progress_rejects_invalid_error_code(error_code: str):
    with pytest.raises(ProgressValidationError):
        emit_progress(step="fetch", status="failed", error_code=error_code)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("source", "1provider"),
        ("source", "bad/source"),
        ("source", "provider\nforged"),
        ("source", "x" * 129),
        ("interface", "_private"),
        ("interface", "bad/interface"),
        ("interface", "prices\tforged"),
        ("interface", "x" * 257),
    ],
)
def test_emit_progress_rejects_invalid_identifiers(field: str, value: str):
    with pytest.raises(ProgressValidationError):
        emit_progress(step="fetch", status="started", **{field: value})


def test_emit_progress_rejects_extra_fields():
    with pytest.raises(ProgressValidationError):
        emit_progress(step="fetch", status="started", message="自由文本")
    with pytest.raises(ProgressValidationError):
        emit_progress(step="fetch", status="started", metadata={"k": "v"})
    with pytest.raises(ProgressValidationError):
        emit_progress(step="fetch", status="started", params={"token": "secret"})
    with pytest.raises(ProgressValidationError):
        emit_progress(step="fetch", status="started", raw="Traceback (most recent call last)")


def test_progress_event_rejects_message_kwarg():
    with pytest.raises(TypeError):
        ProgressEvent(step="fetch", status="started", message="自由文本")


def test_progress_trace_excludes_sensitive_content():
    event = {
        "phase": "data_agent",
        "step": "fetch",
        "status": "failed",
        "message": "调用方自由文本不应出现",
        "source": "akshare",
        "interface": "daily",
        "error_code": "provider_error",
        "metadata": {"secret": "x"},
        "raw": "Traceback (most recent call last): File provider.py",
        "params": {"api_key": "sk-test"},
        "provider_sample": '{"rows":[1,2,3]}',
    }
    trace = progress_to_tool_trace(event)
    trace_str = str(trace)
    for forbidden in (
        "metadata",
        "raw",
        "params",
        "provider_sample",
        "Traceback",
        "api_key",
        "sk-test",
        "调用方自由文本",
        '{"rows"',
    ):
        assert forbidden not in trace_str
    assert trace == {
        "tool": "data_agent.fetch",
        "content": "akshare/daily：数据接口调用失败，错误码 provider_error",
    }
