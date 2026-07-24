from __future__ import annotations

import json
import math
import secrets
import shutil
import tempfile
from pathlib import Path
from typing import Any

from .models import DataAgentLimits, DatasetMeta, JsonValue, sanitize_params_summary

_UNSUPPORTED = "[unsupported]"
_MAX_VALUE_DEPTH = 20
_MAX_SAMPLE_ROWS = 5
_MAX_SAMPLE_FIELDS = 16
_MAX_SAMPLE_DEPTH = 5
_MAX_SAMPLE_ITEMS = 16
_MAX_SAMPLE_STRING = 512
_MAX_SAMPLE_ROW_BYTES = 4_096


def _sanitize_provider_value(value: Any, depth: int = 0) -> JsonValue:
    if depth > _MAX_VALUE_DEPTH:
        return _UNSUPPORTED
    value_type = type(value)
    if value is None or value_type in (bool, int):
        return value
    if value_type is float:
        return value if math.isfinite(value) else _UNSUPPORTED
    if value_type is str:
        return value
    if value_type in (list, tuple):
        return [_sanitize_provider_value(child, depth + 1) for child in value]
    if value_type is dict:
        return {
            key: _sanitize_provider_value(child, depth + 1)
            for key, child in value.items()
            if type(key) is str
        }
    return _UNSUPPORTED


def _sanitize_sample_value(value: JsonValue, depth: int = 0) -> tuple[JsonValue, bool]:
    if depth > _MAX_SAMPLE_DEPTH:
        return "[truncated]", True
    if value is None or type(value) in (bool, int, float):
        return value, False
    if type(value) is str:
        if len(value) <= _MAX_SAMPLE_STRING:
            return value, False
        return value[:_MAX_SAMPLE_STRING], True
    if type(value) is list:
        truncated = len(value) > _MAX_SAMPLE_ITEMS
        output: list[JsonValue] = []
        for child in value[:_MAX_SAMPLE_ITEMS]:
            safe, child_truncated = _sanitize_sample_value(child, depth + 1)
            output.append(safe)
            truncated = truncated or child_truncated
        return output, truncated
    if type(value) is dict:
        truncated = len(value) > _MAX_SAMPLE_ITEMS
        output_dict: dict[str, JsonValue] = {}
        for key, child in list(value.items())[:_MAX_SAMPLE_ITEMS]:
            safe, child_truncated = _sanitize_sample_value(child, depth + 1)
            output_dict[key] = safe
            truncated = truncated or child_truncated
        return output_dict, truncated
    return _UNSUPPORTED, True


def _sample_rows(
    rows: list[dict[str, JsonValue]],
) -> tuple[list[dict[str, JsonValue]], bool]:
    output: list[dict[str, JsonValue]] = []
    truncated = len(rows) > _MAX_SAMPLE_ROWS
    for row in rows[:_MAX_SAMPLE_ROWS]:
        sample_row: dict[str, JsonValue] = {}
        truncated = truncated or len(row) > _MAX_SAMPLE_FIELDS
        for key, value in list(row.items())[:_MAX_SAMPLE_FIELDS]:
            safe, value_truncated = _sanitize_sample_value(value)
            tentative = {**sample_row, key: safe}
            encoded = json.dumps(
                tentative, ensure_ascii=False, allow_nan=False
            ).encode("utf-8")
            if len(encoded) > _MAX_SAMPLE_ROW_BYTES:
                truncated = True
                break
            sample_row[key] = safe
            truncated = truncated or value_truncated
        output.append(sample_row)
    return output, truncated


class DatasetWorkspace:
    def __init__(self, limits: DataAgentLimits, *, root: Path | None = None):
        self.limits = limits
        self.root = root or Path(tempfile.mkdtemp(prefix="share-data-agent-"))
        self._metadata: dict[str, DatasetMeta] = {}
        self._total_rows = 0
        self._total_bytes = 0
        self._python_analysis_calls = 0
        self._sandbox_results: list[JsonValue] = []

    def __enter__(self) -> "DatasetWorkspace":
        self.root.mkdir(parents=True, exist_ok=True)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    @property
    def total_rows(self) -> int:
        return self._total_rows

    @property
    def total_bytes(self) -> int:
        return self._total_bytes

    @property
    def datasets(self) -> list[DatasetMeta]:
        return list(self._metadata.values())

    @property
    def has_sandbox_result(self) -> bool:
        return bool(self._sandbox_results)

    def begin_python_analysis(self) -> bool:
        if self._python_analysis_calls >= self.limits.max_python_retries:
            return False
        self._python_analysis_calls += 1
        return True

    def record_sandbox_result(self, result: JsonValue) -> None:
        self._sandbox_results.append(result)

    def create_dataset(
        self, source: str, interface: str, params: dict[str, Any], payload: dict[str, Any]
    ) -> DatasetMeta:
        raw_rows = list(payload.get("rows") or [])
        if any(type(row) is not dict for row in raw_rows):
            raise ValueError("invalid_provider_rows")
        rows = [
            {
                key: _sanitize_provider_value(value)
                for key, value in row.items()
                if type(key) is str
            }
            for row in raw_rows
        ]
        if len(rows) > self.limits.max_rows_per_fetch:
            raise ValueError("max_rows_per_fetch exceeded")
        encoded = json.dumps(rows, ensure_ascii=False, allow_nan=False).encode()
        if self._total_rows + len(rows) > self.limits.max_total_rows:
            raise ValueError("max_total_rows exceeded")
        if self._total_bytes + len(encoded) > self.limits.max_input_bytes:
            raise ValueError("max_input_bytes exceeded")
        sample, sample_truncated = _sample_rows(rows)
        dataset_id = secrets.token_urlsafe(18)
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / f"{dataset_id}.json").write_bytes(encoded)
        meta = DatasetMeta(
            dataset_id=dataset_id,
            source=source,
            interface=interface,
            params_summary=sanitize_params_summary(params),
            data_time=(
                payload.get("data_time")[:128]
                if type(payload.get("data_time")) is str
                else None
            ),
            columns=[
                value if type(value) is str else _UNSUPPORTED
                for value in payload.get("columns") or []
            ],
            returned=len(rows),
            total=int(payload.get("total") or len(rows)),
            truncated=bool(payload.get("truncated")),
            byte_size=len(encoded),
            sample=sample,
            sample_truncated=sample_truncated,
        )
        self._metadata[dataset_id] = meta
        self._total_rows += len(rows)
        self._total_bytes += len(encoded)
        return meta

    def export(self, dataset_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
        exported: dict[str, list[dict[str, Any]]] = {}
        for dataset_id in dataset_ids:
            if dataset_id not in self._metadata:
                raise KeyError("dataset_not_in_request")
            exported[dataset_id] = json.loads(
                (self.root / f"{dataset_id}.json").read_text(encoding="utf-8")
            )
        return exported
