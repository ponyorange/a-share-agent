from __future__ import annotations

import json
import math
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator

SENSITIVE_KEYS = frozenset(
    {"api_key", "token", "authorization", "password", "secret", "credential"}
)
_MAX_PARAMS_DEPTH = 6
_MAX_PARAMS_ITEMS = 32
_MAX_PARAMS_STRING = 512
_MAX_PARAMS_BYTES = 8 * 1024

ShortText = Annotated[str, Field(min_length=1, max_length=128)]
InterfaceText = Annotated[str, Field(min_length=1, max_length=256)]
ResultText = Annotated[str, Field(max_length=2_048)]


class DataAgentLimits(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    max_rows_per_fetch: int = Field(default=5_000, ge=1, le=5_000)
    max_total_rows: int = Field(default=50_000, ge=1, le=50_000)
    max_input_bytes: int = Field(default=50 * 1024 * 1024, ge=1, le=50 * 1024 * 1024)
    sandbox_timeout_seconds: int = Field(default=30, ge=1, le=30)
    sandbox_memory_mb: int = Field(default=512, ge=128, le=512)
    max_output_bytes: int = Field(default=1024 * 1024, ge=1024, le=1024 * 1024)
    # 语义：首次失败后的「修正重试」次数上限；总调用次数 = max_python_retries + 1
    max_python_retries: int = Field(default=2, ge=0, le=5)
    max_agent_steps: int = Field(default=60, ge=4, le=80)

    @classmethod
    def from_config(cls, value: dict[str, Any] | None) -> "DataAgentLimits":
        return cls.model_validate(value or {})


class DatasetMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset_id: str
    source: str
    interface: str
    params_summary: dict[str, JsonValue]
    data_time: Annotated[str, Field(max_length=128)] | None = None
    columns: list[str]
    returned: int
    total: int
    truncated: bool
    byte_size: int
    sample: list[dict[str, JsonValue]]
    sample_trust: Literal["untrusted_provider_data"] = "untrusted_provider_data"
    sample_truncated: bool


class DataAgentFailure(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: ShortText
    message: Annotated[str, Field(min_length=1, max_length=1_024)]
    source: Annotated[str, Field(max_length=128)] | None = None
    interface: Annotated[str, Field(max_length=256)] | None = None


def _filter_params_summary(value: Any, depth: int = 0) -> JsonValue:
    if depth > _MAX_PARAMS_DEPTH:
        raise ValueError("params_summary_too_deep")
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        if len(value) > _MAX_PARAMS_STRING:
            raise ValueError("params_summary_string_too_long")
        return value
    if isinstance(value, list):
        if len(value) > _MAX_PARAMS_ITEMS:
            raise ValueError("params_summary_too_many_items")
        return [_filter_params_summary(item, depth + 1) for item in value]
    if isinstance(value, dict):
        if len(value) > _MAX_PARAMS_ITEMS:
            raise ValueError("params_summary_too_many_items")
        filtered: dict[str, JsonValue] = {}
        for key, child in value.items():
            if not isinstance(key, str):
                raise ValueError("params_summary_invalid_key")
            if key.casefold() in SENSITIVE_KEYS:
                continue
            if len(key) > 128:
                raise ValueError("params_summary_key_too_long")
            filtered[key] = _filter_params_summary(child, depth + 1)
        return filtered
    raise ValueError("params_summary_not_json")


def sanitize_params_summary(value: Any) -> dict[str, JsonValue]:
    filtered = _filter_params_summary({} if value is None else value)
    if not isinstance(filtered, dict):
        raise ValueError("params_summary_must_be_object")
    encoded = json.dumps(filtered, ensure_ascii=False, allow_nan=False).encode("utf-8")
    if len(encoded) > _MAX_PARAMS_BYTES:
        raise ValueError("params_summary_too_large")
    return filtered


class DataAgentSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: ShortText
    interface: InterfaceText
    params_summary: dict[str, JsonValue] = Field(default_factory=dict)
    data_time: Annotated[str, Field(max_length=128)] | None = None
    rows: int | None = Field(default=None, ge=0, le=50_000)
    truncated: bool | None = None

    @field_validator("params_summary", mode="before")
    @classmethod
    def validate_params_summary(cls, value: Any) -> dict[str, JsonValue]:
        return sanitize_params_summary(value)


class DataAgentResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer: Annotated[str, Field(max_length=16_384)]
    data: JsonValue
    sources: list[DataAgentSource] = Field(max_length=50)
    computation: list[ResultText] = Field(max_length=50)
    warnings: list[ResultText] = Field(max_length=50)
    failures: list[DataAgentFailure] = Field(max_length=50)

    @field_validator("data")
    @classmethod
    def validate_data(cls, value: JsonValue) -> JsonValue:
        _validate_result_data(value)
        return value

    def to_tool_json(self) -> str:
        return json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            allow_nan=False,
        )


def _validate_result_data(value: JsonValue, depth: int = 0) -> None:
    if depth > 20:
        raise ValueError("data_too_deep")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("data_non_finite")
    if isinstance(value, dict):
        for key, child in value.items():
            if key.casefold() in SENSITIVE_KEYS:
                raise ValueError("data_sensitive_key")
            _validate_result_data(child, depth + 1)
    elif isinstance(value, list):
        for child in value:
            _validate_result_data(child, depth + 1)
