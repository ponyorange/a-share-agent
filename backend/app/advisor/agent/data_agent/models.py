from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class DataAgentLimits(BaseModel):
    model_config = ConfigDict(frozen=True)
    max_rows_per_fetch: int = Field(default=5_000, ge=1, le=5_000)
    max_total_rows: int = Field(default=50_000, ge=1, le=50_000)
    max_input_bytes: int = Field(default=50 * 1024 * 1024, ge=1, le=50 * 1024 * 1024)
    sandbox_timeout_seconds: int = Field(default=30, ge=1, le=30)
    sandbox_memory_mb: int = Field(default=512, ge=128, le=512)
    max_output_bytes: int = Field(default=1024 * 1024, ge=1024, le=1024 * 1024)
    max_python_retries: int = Field(default=2, ge=0, le=2)
    max_agent_steps: int = Field(default=24, ge=4, le=40)

    @classmethod
    def from_config(cls, value: dict[str, Any] | None) -> "DataAgentLimits":
        return cls.model_validate(value or {})


class DatasetMeta(BaseModel):
    dataset_id: str
    source: str
    interface: str
    params: dict[str, Any]
    columns: list[str]
    returned: int
    total: int
    truncated: bool
    byte_size: int
    sample: list[dict[str, Any]]


class DataAgentFailure(BaseModel):
    code: str
    message: str
    source: str | None = None
    interface: str | None = None


class DataAgentResult(BaseModel):
    answer: str
    data: Any
    sources: list[dict[str, Any]] = Field(default_factory=list)
    computation: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    failures: list[DataAgentFailure] = Field(default_factory=list)

    def to_tool_json(self) -> str:
        return json.dumps(self.model_dump(mode="json"), ensure_ascii=False)
