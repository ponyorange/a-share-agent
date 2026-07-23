"""AKShare interface catalog via runtime introspection."""

from __future__ import annotations

import inspect
import re
from functools import lru_cache
from typing import Any

import akshare as ak

from .defaults import EXAMPLE_DEFAULTS

# Known data-interface prefixes used by AKShare public APIs
CATEGORY_LABELS: dict[str, str] = {
    "stock": "股票",
    "futures": "期货",
    "option": "期权",
    "bond": "债券",
    "fund": "基金",
    "index": "指数",
    "macro": "宏观",
    "fx": "外汇",
    "currency": "货币",
    "rate": "利率",
    "spot": "现货",
    "energy": "能源",
    "crypto": "加密货币",
    "bank": "银行",
    "air": "空气质量",
    "car": "汽车",
    "movie": "电影",
    "news": "资讯",
    "article": "文章",
    "nlp": "NLP",
    "qdii": "QDII",
    "reits": "REITs",
    "hf": "高频",
    "amac": "私募协会",
    "fortune": "财富榜",
    "sunrise": "日出日落",
    "match": "赛事",
    "sport": "体育",
    "video": "视频",
    "tool": "工具",
    "other": "其他",
}

SKIP_NAMES = {
    "pro_api",
    "set_token",
    "get_token",
}


def _category_of(name: str) -> str:
    prefix = name.split("_", 1)[0]
    if prefix in CATEGORY_LABELS:
        return prefix
    return "other"


def _is_data_interface(name: str, obj: Any) -> bool:
    if name.startswith("_"):
        return False
    if name in SKIP_NAMES:
        return False
    if not callable(obj):
        return False
    # Prefer snake_case public functions that look like data APIs
    if not re.match(r"^[a-z][a-z0-9_]*$", name):
        return False
    # Skip classes
    if inspect.isclass(obj):
        return False
    return True


def _param_info(func: Any) -> list[dict[str, Any]]:
    try:
        sig = inspect.signature(func)
    except (ValueError, TypeError):
        return []

    params: list[dict[str, Any]] = []
    for pname, param in sig.parameters.items():
        if pname in ("self", "cls"):
            continue
        if param.kind in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        ):
            continue

        has_default = param.default is not inspect.Parameter.empty
        default_val = param.default if has_default else None
        # Make JSON-serializable
        if has_default and not isinstance(
            default_val, (str, int, float, bool, type(None), list, dict)
        ):
            default_val = str(default_val)

        annotation = None
        if param.annotation is not inspect.Parameter.empty:
            annotation = getattr(param.annotation, "__name__", str(param.annotation))

        params.append(
            {
                "name": pname,
                "required": not has_default,
                "default": default_val if has_default else None,
                "annotation": annotation,
            }
        )
    return params


def _short_doc(func: Any) -> str:
    doc = inspect.getdoc(func) or ""
    if not doc:
        return ""
    first = doc.strip().split("\n", 1)[0].strip()
    return first[:200]


@lru_cache(maxsize=1)
def build_catalog() -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for name in dir(ak):
        try:
            obj = getattr(ak, name)
        except Exception:
            continue
        if not _is_data_interface(name, obj):
            continue

        category = _category_of(name)
        params = _param_info(obj)
        example = EXAMPLE_DEFAULTS.get(name)
        if example is None:
            example = {
                p["name"]: p["default"]
                for p in params
                if p["default"] is not None
            }

        items.append(
            {
                "name": name,
                "category": category,
                "category_label": CATEGORY_LABELS.get(category, category),
                "doc": _short_doc(obj),
                "params": params,
                "example_params": example,
            }
        )

    items.sort(key=lambda x: (x["category"], x["name"]))
    return items


def get_categories() -> list[dict[str, Any]]:
    catalog = build_catalog()
    counts: dict[str, int] = {}
    for item in catalog:
        counts[item["category"]] = counts.get(item["category"], 0) + 1

    result = []
    # Stable order: known labels first, then remaining alpha
    ordered = list(CATEGORY_LABELS.keys())
    for key in ordered:
        if key in counts:
            result.append(
                {
                    "id": key,
                    "label": CATEGORY_LABELS[key],
                    "count": counts[key],
                }
            )
    for key, count in sorted(counts.items()):
        if key not in CATEGORY_LABELS:
            result.append({"id": key, "label": key, "count": count})
    return result


def list_interfaces(
    category: str | None = None, keyword: str | None = None
) -> list[dict[str, Any]]:
    catalog = build_catalog()
    result = catalog
    if category:
        result = [i for i in result if i["category"] == category]
    if keyword:
        kw = keyword.lower().strip()
        result = [
            i
            for i in result
            if kw in i["name"].lower() or kw in (i["doc"] or "").lower()
        ]
    # Lightweight list payload
    return [
        {
            "name": i["name"],
            "category": i["category"],
            "category_label": i["category_label"],
            "doc": i["doc"],
            "param_count": len(i["params"]),
        }
        for i in result
    ]


def get_interface(name: str) -> dict[str, Any] | None:
    for item in build_catalog():
        if item["name"] == name:
            # Full docstring for detail view
            try:
                func = getattr(ak, name)
                item = {**item, "docstring": inspect.getdoc(func) or ""}
            except Exception:
                item = {**item, "docstring": item.get("doc", "")}
            return item
    return None


def is_allowed(name: str) -> bool:
    return any(i["name"] == name for i in build_catalog())
