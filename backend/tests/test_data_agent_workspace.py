import pytest

from app.advisor.agent.data_agent.models import DataAgentLimits
from app.advisor.agent.data_agent.workspace import DatasetWorkspace


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
