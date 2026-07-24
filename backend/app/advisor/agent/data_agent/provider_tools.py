from __future__ import annotations

import json
from typing import Any

from langchain_core.tools import BaseTool, tool

from app import providers

from .workspace import DatasetWorkspace


def _error_json(
    exc: Exception,
    *,
    source: str | None = None,
    interface: str | None = None,
) -> str:
    code = "internal_error"
    message = "工具执行失败"
    if isinstance(exc, ValueError):
        code = "invalid_params"
        message = "参数错误"
    elif isinstance(exc, KeyError):
        code = "source_unavailable"
        message = "数据源不存在或不可用"
    elif isinstance(exc, LookupError):
        code = "interface_not_found"
        message = "接口不存在"
    elif isinstance(exc, RuntimeError):
        code = "provider_error"
        message = "数据源暂不可用"
    return json.dumps(
        {
            "error": {
                "code": code,
                "message": message,
                "source": source,
                "interface": interface,
            }
        },
        ensure_ascii=False,
    )


def build_provider_tools(workspace: DatasetWorkspace) -> list[BaseTool]:
    @tool
    def list_data_sources() -> str:
        """列出所有已注册数据源及就绪状态。"""
        try:
            return json.dumps(providers.list_sources(), ensure_ascii=False, default=str)
        except Exception as exc:
            return _error_json(exc)

    @tool
    def search_data_interfaces(source: str, keyword: str = "", category: str = "") -> str:
        """按数据源、关键词和分类检索接口目录；调用接口前先检索。"""
        try:
            items = providers.get_provider(source).list_interfaces(
                category=category or None, keyword=keyword or None
            )
            return json.dumps(
                {
                    "source": source,
                    "interfaces": items[:50],
                    "count": len(items),
                    "truncated": len(items) > 50,
                },
                ensure_ascii=False,
                default=str,
            )
        except Exception as exc:
            return _error_json(exc, source=source)

    @tool
    def get_data_interface(source: str, name: str) -> str:
        """读取接口完整参数定义；fetch 前必须调用。"""
        try:
            item = providers.get_provider(source).get_interface(name)
            return json.dumps(
                {"source": source, "interface": item}, ensure_ascii=False, default=str
            )
        except Exception as exc:
            return _error_json(exc, source=source, interface=name)

    @tool
    def fetch_provider_data(
        source: str, name: str, params_json: str = "{}", limit: int = 500
    ) -> str:
        """只读调用任意已注册 Provider 接口并保存为本次请求的数据集。"""
        try:
            params: dict[str, Any] = json.loads(params_json)
            if not isinstance(params, dict):
                raise ValueError("params_json must be an object")
            bounded = max(1, min(int(limit), workspace.limits.max_rows_per_fetch))
            payload = providers.get_provider(source).fetch(name, params, bounded)
            meta = workspace.create_dataset(source, name, params, payload)
            return json.dumps({"dataset": meta.model_dump(mode="json")}, ensure_ascii=False)
        except Exception as exc:
            return _error_json(exc, source=source, interface=name)

    return [
        list_data_sources,
        search_data_interfaces,
        get_data_interface,
        fetch_provider_data,
    ]
