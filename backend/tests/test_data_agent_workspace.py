import json

import pytest

from app.advisor.agent.data_agent.models import DataAgentLimits
from app.advisor.agent.data_agent.workspace import DatasetWorkspace


class Evil:
    calls = 0

    def __str__(self):
        type(self).calls += 1
        return "EVIL_SECRET"


def test_workspace_enforces_total_rows_and_removes_files(tmp_path):
    limits = DataAgentLimits(max_total_rows=2)
    path = tmp_path / "request"
    with DatasetWorkspace(limits, root=path) as workspace:
        meta = workspace.create_dataset(
            "akshare",
            "demo",
            {},
            {
                "columns": ["x"],
                "rows": [{"x": 1}, {"x": 2}],
                "returned": 2,
                "total": 2,
                "truncated": False,
            },
        )
        assert workspace.export([meta.dataset_id])[meta.dataset_id] == [{"x": 1}, {"x": 2}]
        with pytest.raises(ValueError, match="max_total_rows"):
            workspace.create_dataset(
                "akshare",
                "demo2",
                {},
                {
                    "columns": ["x"],
                    "rows": [{"x": 3}],
                    "returned": 1,
                    "total": 1,
                    "truncated": False,
                },
            )
    assert not path.exists()


def test_workspace_enforces_input_bytes_without_storing_dataset(tmp_path):
    limits = DataAgentLimits(max_input_bytes=8)
    with DatasetWorkspace(limits, root=tmp_path / "request") as workspace:
        with pytest.raises(ValueError, match="max_input_bytes"):
            workspace.create_dataset(
                "akshare",
                "wide",
                {},
                {
                    "columns": ["token"],
                    "rows": [{"token": "too-large"}],
                    "returned": 1,
                    "total": 1,
                    "truncated": False,
                },
            )
        assert workspace.datasets == []
        assert workspace.total_rows == 0
        assert workspace.total_bytes == 0


def test_workspace_enforces_rows_per_fetch_without_storing_dataset(tmp_path):
    limits = DataAgentLimits(max_rows_per_fetch=2)
    with DatasetWorkspace(limits, root=tmp_path / "request") as workspace:
        with pytest.raises(ValueError, match="^max_rows_per_fetch exceeded$"):
            workspace.create_dataset(
                "akshare",
                "ignored-limit",
                {},
                {
                    "columns": ["x"],
                    "rows": [{"x": 1}, {"x": 2}, {"x": 3}],
                    "returned": 3,
                    "total": 3,
                    "truncated": False,
                },
            )
        assert workspace.datasets == []
        assert workspace.total_rows == 0
        assert workspace.total_bytes == 0


def test_workspace_isolates_dataset_ids_between_requests(tmp_path):
    with DatasetWorkspace(DataAgentLimits(), root=tmp_path / "request-a") as first:
        first_meta = first.create_dataset(
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
        with DatasetWorkspace(DataAgentLimits(), root=tmp_path / "request-b") as second:
            second_meta = second.create_dataset(
                "akshare",
                "demo",
                {},
                {
                    "columns": ["x"],
                    "rows": [{"x": 2}],
                    "returned": 1,
                    "total": 1,
                    "truncated": False,
                },
            )
            assert first_meta.dataset_id != second_meta.dataset_id
            with pytest.raises(KeyError, match="dataset_not_in_request"):
                second.export([first_meta.dataset_id])


def test_workspace_removes_files_after_failure(tmp_path):
    path = tmp_path / "request"
    with pytest.raises(RuntimeError, match="boom"):
        with DatasetWorkspace(DataAgentLimits(), root=path) as workspace:
            workspace.create_dataset(
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
            assert path.exists()
            raise RuntimeError("boom")
    assert not path.exists()


def test_workspace_records_bounded_canonical_sandbox_evidence(tmp_path):
    with DatasetWorkspace(
        DataAgentLimits(max_python_retries=2), root=tmp_path / "request"
    ) as workspace:
        first = workspace.record_sandbox_result({"z": 1, "a": [2.0]})
        second = workspace.record_sandbox_result({"ok": True})
        third = workspace.record_sandbox_result({"third": True})

        assert first.result == {"a": [2.0], "z": 1}
        assert first.result_id != second.result_id
        assert third.result == {"third": True}
        assert first.summary == {"type": "object", "bytes": 17}
        assert workspace.matches_sandbox_result({"a": [2.0], "z": 1})
        assert workspace.matches_sandbox_result(
            {
                "result_id": first.result_id,
                "payload": {"z": 1, "a": [2.0]},
            }
        )
        assert not workspace.matches_sandbox_result({"z": 999, "a": [2.0]})
        with pytest.raises(ValueError, match="sandbox_result_limit_exceeded"):
            workspace.record_sandbox_result({"fourth": True})


def test_dataset_metadata_bounds_and_sanitizes_untrusted_samples(tmp_path):
    Evil.calls = 0
    rows = [
        {
            "evil": Evil(),
            "not_finite": float("nan"),
            "large": "x" * 20_000,
            **{f"field_{field}": field for field in range(100)},
        }
        for _ in range(20)
    ]

    with DatasetWorkspace(DataAgentLimits(), root=tmp_path / "request") as workspace:
        meta = workspace.create_dataset(
            "akshare",
            "wide",
            {},
            {
                "columns": list(rows[0]),
                "rows": rows,
                "returned": len(rows),
                "total": len(rows),
                "truncated": False,
            },
        )

        assert meta.sample_trust == "untrusted_provider_data"
        assert meta.sample_truncated is True
        assert len(meta.sample) <= 5
        assert all(len(row) <= 16 for row in meta.sample)
        assert all(
            len(json.dumps(row, ensure_ascii=False, allow_nan=False).encode("utf-8"))
            <= 4_096
            for row in meta.sample
        )
        assert meta.sample[0]["evil"] == "[unsupported]"
        assert meta.sample[0]["not_finite"] == "[unsupported]"
        assert Evil.calls == 0
        assert "EVIL_SECRET" not in json.dumps(meta.model_dump(mode="json"), ensure_ascii=False)


def test_dataset_metadata_returns_only_redacted_params_summary(tmp_path):
    with DatasetWorkspace(DataAgentLimits(), root=tmp_path / "request") as workspace:
        meta = workspace.create_dataset(
            "tushare",
            "daily",
            {
                "ts_code": "600519.SH",
                "token": "secret-token",
                "nested": {"authorization": "Bearer secret", "period": "D"},
            },
            {
                "columns": ["close"],
                "rows": [{"close": 100}],
                "returned": 1,
                "total": 1,
                "truncated": False,
                "data_time": "2026-07-24",
            },
        )

        payload = meta.model_dump(mode="json")
        assert "params" not in payload
        assert payload["params_summary"] == {
            "ts_code": "600519.SH",
            "nested": {"period": "D"},
        }
        assert payload["data_time"] == "2026-07-24"
        assert "secret" not in json.dumps(payload, ensure_ascii=False)
