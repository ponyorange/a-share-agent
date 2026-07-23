"""Normalize arbitrary Python/pandas results into JSON-safe table payloads."""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd


def json_safe(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return value
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        f = float(value)
        if math.isnan(f) or math.isinf(f):
            return None
        return f
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, (pd.Timedelta,)):
        return str(value)
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    return str(value)


def normalize_result(data: Any, limit: int) -> dict[str, Any]:
    if isinstance(data, pd.DataFrame):
        total = len(data)
        truncated = total > limit
        df = data.head(limit)
        columns = [str(c) for c in df.columns.tolist()]
        rows = []
        for record in df.to_dict(orient="records"):
            rows.append({str(k): json_safe(v) for k, v in record.items()})
        return {
            "type": "dataframe",
            "columns": columns,
            "rows": rows,
            "total": total,
            "truncated": truncated,
            "returned": len(rows),
        }

    if isinstance(data, pd.Series):
        df = data.reset_index()
        df.columns = ["index", "value"] if len(df.columns) == 2 else df.columns
        return normalize_result(df, limit)

    if isinstance(data, dict):
        if all(
            not isinstance(v, (dict, list, pd.DataFrame, pd.Series))
            for v in data.values()
        ):
            columns = [str(k) for k in data.keys()]
            row = {str(k): json_safe(v) for k, v in data.items()}
            return {
                "type": "dict",
                "columns": columns,
                "rows": [row],
                "total": 1,
                "truncated": False,
                "returned": 1,
            }
        return {
            "type": "json",
            "columns": ["key", "value"],
            "rows": [{"key": str(k), "value": json_safe(v)} for k, v in data.items()],
            "total": len(data),
            "truncated": False,
            "returned": len(data),
            "raw": json_safe(data),
        }

    if isinstance(data, (list, tuple)):
        if not data:
            return {
                "type": "list",
                "columns": ["value"],
                "rows": [],
                "total": 0,
                "truncated": False,
                "returned": 0,
            }
        if all(isinstance(x, dict) for x in data):
            keys: list[str] = []
            seen = set()
            for item in data:
                for k in item.keys():
                    sk = str(k)
                    if sk not in seen:
                        seen.add(sk)
                        keys.append(sk)
            total = len(data)
            truncated = total > limit
            slice_data = data[:limit]
            rows = [{k: json_safe(item.get(k)) for k in keys} for item in slice_data]
            return {
                "type": "list",
                "columns": keys,
                "rows": rows,
                "total": total,
                "truncated": truncated,
                "returned": len(rows),
            }
        total = len(data)
        truncated = total > limit
        rows = [{"value": json_safe(v)} for v in data[:limit]]
        return {
            "type": "list",
            "columns": ["value"],
            "rows": rows,
            "total": total,
            "truncated": truncated,
            "returned": len(rows),
        }

    return {
        "type": "scalar",
        "columns": ["value"],
        "rows": [{"value": json_safe(data)}],
        "total": 1,
        "truncated": False,
        "returned": 1,
    }
