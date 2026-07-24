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


class OversizedMetadataProvider:
    def list_interfaces(self, category=None, keyword=None):
        return [
            {
                "name": "prices",
                "category": "market",
                "category_label": "行情",
                "doc": "x" * 100_000,
                "param_count": 200,
                "authorization": "Bearer secret",
                "irrelevant_blob": {"raw": "do-not-return"},
            }
        ]

    def get_interface(self, name):
        nested = "leaf"
        for _ in range(12):
            nested = {"child": nested}
        return {
            "name": name,
            "category": "market",
            "category_label": "行情",
            "doc": "d" * 100_000,
            "docstring": "s" * 100_000,
            "params": [
                {
                    "name": f"p{index}",
                    "required": False,
                    "default": nested,
                    "annotation": "str",
                    "description": "p" * 10_000,
                    "token": "secret",
                }
                for index in range(200)
            ],
            "example_params": {
                "symbol": "600519",
                "nested": nested,
                "api_key": "secret",
            },
            "raw_schema": {"do": "not return"},
        }


class Evil:
    calls = 0

    def __str__(self):
        type(self).calls += 1
        return "EVIL_SECRET"

    def __repr__(self):
        type(self).calls += 1
        raise RuntimeError("repr must not run")


class EvilMetadataProvider:
    def __init__(self, evil):
        self.evil = evil

    def list_interfaces(self, category=None, keyword=None):
        return [
            {
                "name": self.evil,
                "category": "market",
                "doc": self.evil,
                "param_count": self.evil,
            }
        ]

    def get_interface(self, name):
        return {
            "name": self.evil,
            "category": "market",
            "doc": self.evil,
            "params": [
                {
                    "name": self.evil,
                    "required": self.evil,
                    "default": {
                        self.evil: self.evil,
                        "nested": (self.evil, float("nan"), "safe"),
                    },
                },
                {
                    "name": "period",
                    "required": False,
                    "default": ("daily", "weekly"),
                    "annotation": "str",
                },
            ],
            "example_params": {"symbol": self.evil},
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
            listed = json.loads(tools["list_data_sources"].invoke({}))
            assert listed[0]["id"] == "fake"
            assert listed[0]["trust"] == "untrusted_provider_metadata"
            searched = json.loads(
                tools["search_data_interfaces"].invoke(
                    {"source": "fake", "keyword": "price", "category": "market"}
                )
            )
            assert searched["count"] == 1
            assert searched["trust"] == "untrusted_provider_metadata"
            detail = json.loads(
                tools["get_data_interface"].invoke({"source": "fake", "name": "prices"})
            )
            assert detail["interface"]["name"] == "prices"
            assert detail["interface"]["params"] == [
                {"name": "symbol", "required": True}
            ]
            assert detail["trust"] == "untrusted_provider_metadata"
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


def test_provider_metadata_is_whitelisted_truncated_and_byte_bounded(tmp_path):
    with DatasetWorkspace(DataAgentLimits(), root=tmp_path / "r") as workspace:
        tools = {tool.name: tool for tool in build_provider_tools(workspace)}
        with patch(
            "app.advisor.agent.data_agent.provider_tools.providers.get_provider",
            return_value=OversizedMetadataProvider(),
        ):
            search_raw = tools["search_data_interfaces"].invoke(
                {"source": "fake", "keyword": "", "category": ""}
            )
            detail_raw = tools["get_data_interface"].invoke(
                {"source": "fake", "name": "prices"}
            )

    assert len(search_raw.encode("utf-8")) <= 64 * 1024
    search = json.loads(search_raw)
    item = search["interfaces"][0]
    assert item["trust"] == "untrusted_provider_metadata"
    assert len(item["doc"]) <= 2_048
    assert "authorization" not in item
    assert "irrelevant_blob" not in item

    assert len(detail_raw.encode("utf-8")) <= 64 * 1024
    detail = json.loads(detail_raw)
    interface = detail["interface"]
    assert detail["trust"] == "untrusted_provider_metadata"
    assert set(interface) <= {
        "name",
        "category",
        "category_label",
        "doc",
        "docstring",
        "params",
        "example_params",
        "truncated",
    }
    assert len(interface["doc"]) <= 2_048
    assert len(interface["docstring"]) <= 2_048
    assert len(interface["params"]) <= 64
    assert "secret" not in detail_raw
    assert "raw_schema" not in interface
    assert "truncated" in detail_raw


def test_data_source_metadata_is_truncated_to_total_byte_limit(tmp_path):
    oversized_sources = [
        {
            "id": f"source-{index}",
            "label": "x" * 10_000,
            "features": ["y" * 10_000] * 100,
        }
        for index in range(100)
    ]
    with DatasetWorkspace(DataAgentLimits(), root=tmp_path / "r") as workspace:
        tool = {
            item.name: item for item in build_provider_tools(workspace)
        }["list_data_sources"]
        with patch(
            "app.advisor.agent.data_agent.provider_tools.providers.list_sources",
            return_value=oversized_sources,
        ):
            raw = tool.invoke({})

    assert len(raw.encode("utf-8")) <= 64 * 1024
    payload = json.loads(raw)
    assert isinstance(payload, list)
    assert payload
    assert all(item["trust"] == "untrusted_provider_metadata" for item in payload)
    assert payload[-1].get("truncated") is True


def test_provider_metadata_never_stringifies_unsupported_objects(tmp_path):
    Evil.calls = 0
    evil = Evil()
    provider = EvilMetadataProvider(evil)
    with DatasetWorkspace(DataAgentLimits(), root=tmp_path / "r") as workspace:
        tools = {item.name: item for item in build_provider_tools(workspace)}
        with (
            patch(
                "app.advisor.agent.data_agent.provider_tools.providers.list_sources",
                return_value=[
                    {
                        "id": evil,
                        "label": "Safe",
                        "features": [evil, float("inf")],
                    }
                ],
            ),
            patch(
                "app.advisor.agent.data_agent.provider_tools.providers.get_provider",
                return_value=provider,
            ),
        ):
            listed_raw = tools["list_data_sources"].invoke({})
            searched_raw = tools["search_data_interfaces"].invoke(
                {"source": "fake", "keyword": "", "category": ""}
            )
            detail_raw = tools["get_data_interface"].invoke(
                {"source": "fake", "name": "prices"}
            )

    assert Evil.calls == 0
    combined = listed_raw + searched_raw + detail_raw
    assert "EVIL_SECRET" not in combined

    listed = json.loads(listed_raw)
    assert listed[0]["id"] == "[unsupported]"
    assert listed[0]["features"] == ["[unsupported]", "[unsupported]"]
    assert listed[0]["truncated"] is True

    searched = json.loads(searched_raw)
    assert searched["interfaces"][0]["name"] == "[unsupported]"
    assert searched["interfaces"][0]["doc"] == "[unsupported]"
    assert searched["truncated"] is True

    detail = json.loads(detail_raw)["interface"]
    assert detail["name"] == "[unsupported]"
    assert detail["doc"] == "[unsupported]"
    assert detail["params"][0]["default"] == {
        "nested": ["[unsupported]", "[unsupported]", "safe"]
    }
    assert detail["params"][1]["default"] == ["daily", "weekly"]
    assert detail["truncated"] is True


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
