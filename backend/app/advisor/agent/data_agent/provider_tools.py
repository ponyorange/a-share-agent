from __future__ import annotations

import json
import math
from typing import Any

from langchain_core.tools import BaseTool, tool

from app import providers

from ..progress import emit_progress, validate_progress_identifier
from .models import SENSITIVE_KEYS
from .workspace import DatasetWorkspace

_METADATA_TRUST = "untrusted_provider_metadata"
_MAX_METADATA_BYTES = 64 * 1024
_MAX_METADATA_DEPTH = 5
_MAX_METADATA_ITEMS = 32
_MAX_METADATA_STRING = 512
_MAX_DOC_STRING = 2_048
_MAX_INTERFACES = 10
_MAX_PARAMS = 64
_UNSUPPORTED = "[unsupported]"


def _truncate_text(value: Any, limit: int = _MAX_METADATA_STRING) -> tuple[str, bool]:
    if type(value) is not str:
        return _UNSUPPORTED, True
    if len(value) <= limit:
        return value, False
    return value[:limit], True


def _sanitize_metadata_value(
    value: Any,
    depth: int = 0,
    budget: list[int] | None = None,
) -> tuple[Any, bool]:
    if budget is None:
        budget = [512]
    budget[0] -= 1
    if budget[0] < 0:
        return "[truncated]", True
    if depth > _MAX_METADATA_DEPTH:
        return "[truncated]", True
    value_type = type(value)
    if value is None or value_type in (bool, int):
        return value, False
    if value_type is float:
        return (value, False) if math.isfinite(value) else (_UNSUPPORTED, True)
    if value_type is str:
        return _truncate_text(value)
    if value_type in (list, tuple):
        truncated = len(value) > _MAX_METADATA_ITEMS
        output = []
        for child in value[:_MAX_METADATA_ITEMS]:
            safe, child_truncated = _sanitize_metadata_value(
                child, depth + 1, budget
            )
            output.append(safe)
            truncated = truncated or child_truncated
        return output, truncated
    if value_type is dict:
        truncated = len(value) > _MAX_METADATA_ITEMS
        output: dict[str, Any] = {}
        for raw_key, child in list(value.items())[:_MAX_METADATA_ITEMS]:
            if type(raw_key) is not str:
                truncated = True
                continue
            key, key_truncated = _truncate_text(raw_key, 128)
            if key.casefold() in SENSITIVE_KEYS:
                truncated = True
                continue
            safe, child_truncated = _sanitize_metadata_value(
                child, depth + 1, budget
            )
            output[key] = safe
            truncated = truncated or key_truncated or child_truncated
        return output, truncated
    return _UNSUPPORTED, True


def _bounded_value(value: Any, byte_limit: int) -> tuple[Any, bool]:
    safe, truncated = _sanitize_metadata_value(value)
    if len(json.dumps(safe, ensure_ascii=False).encode("utf-8")) > byte_limit:
        return "[truncated]", True
    return safe, truncated


def _source_item(item: Any) -> dict[str, Any]:
    raw = item if type(item) is dict else {}
    output: dict[str, Any] = {"trust": _METADATA_TRUST}
    truncated = type(item) is not dict
    for key in ("id", "label", "ready", "version", "interface_count", "error"):
        if key not in raw:
            continue
        value, item_truncated = _bounded_value(raw[key], 2_048)
        output[key] = value
        truncated = truncated or item_truncated
    if "features" in raw:
        output["features"], item_truncated = _bounded_value(raw["features"], 4_096)
        truncated = truncated or item_truncated
    output["truncated"] = truncated
    return output


def _interface_summary(item: Any) -> tuple[dict[str, Any], bool]:
    raw = item if type(item) is dict else {}
    output: dict[str, Any] = {"trust": _METADATA_TRUST}
    truncated = type(item) is not dict
    for key in ("name", "category", "category_label"):
        if key in raw:
            output[key], item_truncated = _truncate_text(raw[key], 256)
            truncated = truncated or item_truncated
    if "doc" in raw:
        output["doc"], item_truncated = _truncate_text(raw["doc"], _MAX_DOC_STRING)
        truncated = truncated or item_truncated
    if "param_count" in raw:
        if type(raw["param_count"]) is int:
            output["param_count"] = max(0, min(raw["param_count"], 100_000))
        else:
            output["param_count"] = _UNSUPPORTED
            truncated = True
    return output, truncated


def _parameter_definition(item: Any) -> tuple[dict[str, Any], bool]:
    raw = item if type(item) is dict else {}
    output: dict[str, Any] = {}
    truncated = type(item) is not dict
    for key in ("name", "annotation", "type"):
        if key in raw:
            output[key], item_truncated = _truncate_text(raw[key], 256)
            truncated = truncated or item_truncated
    for key in ("description", "doc"):
        if key in raw:
            output[key], item_truncated = _truncate_text(raw[key], 512)
            truncated = truncated or item_truncated
    if "required" in raw:
        if type(raw["required"]) is bool:
            output["required"] = raw["required"]
        else:
            output["required"] = _UNSUPPORTED
            truncated = True
    for key in ("default", "enum", "choices"):
        if key in raw:
            output[key], item_truncated = _bounded_value(raw[key], 2_048)
            truncated = truncated or item_truncated
    for key in raw:
        if type(key) is not str or key.casefold() in SENSITIVE_KEYS:
            truncated = True
    return output, truncated


def _interface_detail(item: Any) -> dict[str, Any]:
    if type(item) is not dict:
        return {"truncated": True}
    output: dict[str, Any] = {}
    truncated = False
    for key in ("name", "category", "category_label"):
        if key in item:
            output[key], item_truncated = _truncate_text(item[key], 256)
            truncated = truncated or item_truncated
    for key in ("doc", "docstring"):
        if key in item:
            output[key], item_truncated = _truncate_text(item[key], _MAX_DOC_STRING)
            truncated = truncated or item_truncated

    params = item.get("params")
    if type(params) in (list, tuple):
        truncated = truncated or len(params) > _MAX_PARAMS
        output["params"] = []
        for raw_param in params[:_MAX_PARAMS]:
            param, param_truncated = _parameter_definition(raw_param)
            tentative = {**output, "params": [*output["params"], param]}
            if len(json.dumps(tentative, ensure_ascii=False).encode("utf-8")) > 56 * 1024:
                truncated = True
                break
            output["params"].append(param)
            truncated = truncated or param_truncated
    elif "params" in item:
        output["params"] = []
        truncated = True
    if "example_params" in item:
        output["example_params"], item_truncated = _bounded_value(
            item["example_params"], 4_096
        )
        truncated = truncated or item_truncated
    allowed = {
        "name",
        "category",
        "category_label",
        "doc",
        "docstring",
        "params",
        "example_params",
    }
    output["truncated"] = truncated
    for key in item:
        if type(key) is not str or key not in allowed:
            output["truncated"] = True
    return output


def _metadata_json(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False)
    if len(encoded.encode("utf-8")) > _MAX_METADATA_BYTES:
        raise ValueError("provider_metadata_too_large")
    return encoded


def _error_json(
    exc: Exception,
    *,
    source: str | None = None,
    interface: str | None = None,
) -> str:
    code, message = _error_code_and_message(exc)
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


def _error_code_and_message(exc: Exception) -> tuple[str, str]:
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
    return code, message


def build_provider_tools(workspace: DatasetWorkspace) -> list[BaseTool]:
    @tool
    def list_data_sources() -> str:
        """列出所有已注册数据源及就绪状态。"""
        emit_progress(step="list_sources", status="started")
        try:
            raw_value = providers.list_sources()
            if type(raw_value) not in (list, tuple):
                raw_sources = []
                sources = [{"trust": _METADATA_TRUST, "truncated": True}]
                emit_progress(step="list_sources", status="completed")
                return _metadata_json(sources)
            raw_sources = raw_value
            sources = []
            for raw_item in raw_sources[:50]:
                item = _source_item(raw_item)
                tentative = [*sources, item]
                if len(json.dumps(tentative, ensure_ascii=False).encode("utf-8")) > 60 * 1024:
                    if sources:
                        sources[-1]["truncated"] = True
                    break
                sources.append(item)
            if len(raw_sources) > len(sources) and sources:
                sources[-1]["truncated"] = True
            emit_progress(step="list_sources", status="completed")
            return _metadata_json(sources)
        except Exception as exc:
            emit_progress(
                step="list_sources",
                status="failed",
                error_code=_error_code_and_message(exc)[0],
            )
            return _error_json(exc)

    @tool
    def search_data_interfaces(source: str, keyword: str = "", category: str = "") -> str:
        """按数据源、关键词和分类检索接口目录；调用接口前先检索。"""
        safe_source: str | None = None
        error_source: str | None = None
        try:
            validated_source = validate_progress_identifier(source, field="source")
            error_source = validated_source
            provider = providers.get_provider(validated_source)
            safe_source = validated_source
            emit_progress(step="search", status="started", source=safe_source)
            items = provider.list_interfaces(
                category=category or None, keyword=keyword or None
            )
            if type(items) not in (list, tuple):
                items = ()
                collection_truncated = True
            else:
                collection_truncated = False
            output = {
                "source": safe_source,
                "trust": _METADATA_TRUST,
                "interfaces": [],
                "count": len(items),
                "truncated": collection_truncated
                or len(items) > _MAX_INTERFACES,
            }
            for raw_item in items[:_MAX_INTERFACES]:
                item, item_truncated = _interface_summary(raw_item)
                tentative = {**output, "interfaces": [*output["interfaces"], item]}
                if len(_metadata_json(tentative).encode("utf-8")) > 60 * 1024:
                    output["truncated"] = True
                    break
                output["interfaces"].append(item)
                output["truncated"] = output["truncated"] or item_truncated
            emit_progress(step="search", status="completed", source=safe_source)
            return _metadata_json(output)
        except Exception as exc:
            emit_progress(
                step="search",
                status="failed",
                source=safe_source,
                error_code=_error_code_and_message(exc)[0],
            )
            return _error_json(exc, source=error_source)

    @tool
    def get_data_interface(source: str, name: str) -> str:
        """读取接口完整参数定义；fetch 前必须调用。"""
        safe_source: str | None = None
        safe_interface: str | None = None
        error_source: str | None = None
        error_interface: str | None = None
        try:
            validated_source = validate_progress_identifier(source, field="source")
            validated_interface = validate_progress_identifier(name, field="interface")
            error_source = validated_source
            error_interface = validated_interface
            provider = providers.get_provider(validated_source)
            item = provider.get_interface(validated_interface)
            safe_source = validated_source
            safe_interface = validated_interface
            emit_progress(
                step="describe",
                status="started",
                source=safe_source,
                interface=safe_interface,
            )
            payload = _metadata_json(
                {
                    "source": safe_source,
                    "trust": _METADATA_TRUST,
                    "interface": _interface_detail(item),
                }
            )
            emit_progress(
                step="describe",
                status="completed",
                source=safe_source,
                interface=safe_interface,
            )
            return payload
        except Exception as exc:
            emit_progress(
                step="describe",
                status="failed",
                source=safe_source,
                interface=safe_interface,
                error_code=_error_code_and_message(exc)[0],
            )
            return _error_json(
                exc,
                source=error_source,
                interface=error_interface,
            )

    @tool
    def fetch_provider_data(
        source: str, name: str, params_json: str = "{}", limit: int = 500
    ) -> str:
        """只读调用任意已注册 Provider 接口并保存为本次请求的数据集。"""
        safe_source: str | None = None
        safe_interface: str | None = None
        error_source: str | None = None
        error_interface: str | None = None
        try:
            validated_source = validate_progress_identifier(source, field="source")
            validated_interface = validate_progress_identifier(name, field="interface")
            error_source = validated_source
            error_interface = validated_interface
            provider = providers.get_provider(validated_source)
            provider.get_interface(validated_interface)
            safe_source = validated_source
            safe_interface = validated_interface
            emit_progress(
                step="fetch",
                status="started",
                source=safe_source,
                interface=safe_interface,
            )
            params: dict[str, Any] = json.loads(params_json)
            if not isinstance(params, dict):
                raise ValueError("params_json must be an object")
            bounded = max(1, min(int(limit), workspace.limits.max_rows_per_fetch))
            payload = provider.fetch(safe_interface, params, bounded)
            meta = workspace.create_dataset(
                safe_source,
                safe_interface,
                params,
                payload,
            )
            emit_progress(
                step="fetch",
                status="completed",
                source=safe_source,
                interface=safe_interface,
                rows=meta.returned,
                truncated=meta.truncated,
            )
            return json.dumps({"dataset": meta.model_dump(mode="json")}, ensure_ascii=False)
        except Exception as exc:
            emit_progress(
                step="fetch",
                status="failed",
                source=safe_source,
                interface=safe_interface,
                error_code=_error_code_and_message(exc)[0],
            )
            return _error_json(
                exc,
                source=error_source,
                interface=error_interface,
            )

    return [
        list_data_sources,
        search_data_interfaces,
        get_data_interface,
        fetch_provider_data,
    ]
