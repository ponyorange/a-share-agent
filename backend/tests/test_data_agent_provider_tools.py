import json
from unittest.mock import patch

from app.advisor.agent.data_agent.models import DataAgentLimits
from app.advisor.agent.data_agent.provider_tools import build_provider_tools
from app.advisor.agent.data_agent.workspace import DatasetWorkspace


class FakeProvider:
    def list_interfaces(self, category=None, keyword=None):
        return [{"name": "prices", "category": "market", "doc": "价格", "param_count": 1}]

    def get_interface(self, name):
        return {"name": name, "params": [{"name": "symbol", "required": True}]}

    def fetch(self, name, params, limit):
        rows = [{"close": value} for value in range(limit)]
        return {
            "name": name,
            "params": params,
            "columns": ["close"],
            "rows": rows,
            "returned": len(rows),
            "total": len(rows),
            "truncated": False,
        }


class LimitIgnoringProvider:
    def fetch(self, name, params, limit):
        rows = [{"close": value} for value in range(5_001)]
        return {
            "name": name,
            "params": params,
            "columns": ["close"],
            "rows": rows,
            "returned": len(rows),
            "total": len(rows),
            "truncated": False,
        }


def test_provider_tools_discover_and_store_without_exposing_full_dataset(tmp_path):
    with DatasetWorkspace(DataAgentLimits(), root=tmp_path / "r") as workspace:
        tools = {tool.name: tool for tool in build_provider_tools(workspace)}
        with (
            patch(
                "app.advisor.agent.data_agent.provider_tools.providers.list_sources",
                return_value=[{"id": "fake", "label": "Fake"}],
            ),
            patch(
                "app.advisor.agent.data_agent.provider_tools.providers.get_provider",
                return_value=FakeProvider(),
            ),
        ):
            assert json.loads(tools["list_data_sources"].invoke({}))[0]["id"] == "fake"
            searched = json.loads(
                tools["search_data_interfaces"].invoke(
                    {"source": "fake", "keyword": "price", "category": "market"}
                )
            )
            assert searched["count"] == 1
            assert json.loads(
                tools["get_data_interface"].invoke({"source": "fake", "name": "prices"})
            )["interface"]["name"] == "prices"
            fetched = json.loads(
                tools["fetch_provider_data"].invoke(
                    {
                        "source": "fake",
                        "name": "prices",
                        "params_json": '{"symbol":"000001"}',
                        "limit": 100,
                    }
                )
            )
        assert fetched["dataset"]["returned"] == 100
        assert "rows" not in fetched["dataset"]
        assert len(fetched["dataset"]["sample"]) == 5
        exported = workspace.export([fetched["dataset"]["dataset_id"]])
        assert len(exported[fetched["dataset"]["dataset_id"]]) == 100


def test_fetch_provider_data_bounds_limit_to_workspace_limits(tmp_path):
    limits = DataAgentLimits(max_rows_per_fetch=3)
    with DatasetWorkspace(limits, root=tmp_path / "r") as workspace:
        tools = {tool.name: tool for tool in build_provider_tools(workspace)}
        with patch(
            "app.advisor.agent.data_agent.provider_tools.providers.get_provider",
            return_value=FakeProvider(),
        ):
            fetched = json.loads(
                tools["fetch_provider_data"].invoke(
                    {"source": "fake", "name": "prices", "params_json": "{}", "limit": 100}
                )
            )
        assert fetched["dataset"]["returned"] == 3


def test_fetch_provider_data_rejects_provider_that_ignores_bounded_limit(tmp_path):
    with DatasetWorkspace(DataAgentLimits(), root=tmp_path / "r") as workspace:
        tools = {tool.name: tool for tool in build_provider_tools(workspace)}
        with patch(
            "app.advisor.agent.data_agent.provider_tools.providers.get_provider",
            return_value=LimitIgnoringProvider(),
        ):
            payload = json.loads(
                tools["fetch_provider_data"].invoke(
                    {"source": "fake", "name": "prices", "params_json": "{}", "limit": 100}
                )
            )
        assert payload == {
            "error": {
                "code": "invalid_params",
                "message": "参数错误",
                "source": "fake",
                "interface": "prices",
            }
        }
        assert workspace.datasets == []


def test_provider_errors_are_stable_and_do_not_leak_details(tmp_path):
    secret = "TOKEN=secret-value"
    with DatasetWorkspace(DataAgentLimits(), root=tmp_path / "r") as workspace:
        tools = {tool.name: tool for tool in build_provider_tools(workspace)}
        with patch(
            "app.advisor.agent.data_agent.provider_tools.providers.get_provider",
            side_effect=RuntimeError(f"provider failed with {secret}"),
        ):
            payload = json.loads(
                tools["fetch_provider_data"].invoke(
                    {
                        "source": "fake",
                        "name": "prices",
                        "params_json": "{}",
                        "limit": 100,
                    }
                )
            )
        assert payload == {
            "error": {
                "code": "provider_error",
                "message": "数据源暂不可用",
                "source": "fake",
                "interface": "prices",
            }
        }
        assert secret not in json.dumps(payload, ensure_ascii=False)
        assert "Traceback" not in json.dumps(payload, ensure_ascii=False)


def test_invalid_params_error_includes_source_and_interface(tmp_path):
    with DatasetWorkspace(DataAgentLimits(), root=tmp_path / "r") as workspace:
        tools = {tool.name: tool for tool in build_provider_tools(workspace)}
        payload = json.loads(
            tools["fetch_provider_data"].invoke(
                {
                    "source": "akshare",
                    "name": "stock_zh_a_hist",
                    "params_json": "{not-json",
                    "limit": 100,
                }
            )
        )
        assert payload == {
            "error": {
                "code": "invalid_params",
                "message": "参数错误",
                "source": "akshare",
                "interface": "stock_zh_a_hist",
            }
        }
