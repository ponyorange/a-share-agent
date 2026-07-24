from __future__ import annotations

import json
import secrets
import shutil
import tempfile
from pathlib import Path
from typing import Any

from .models import DataAgentLimits, DatasetMeta


class DatasetWorkspace:
    def __init__(self, limits: DataAgentLimits, *, root: Path | None = None):
        self.limits = limits
        self.root = root or Path(tempfile.mkdtemp(prefix="share-data-agent-"))
        self._metadata: dict[str, DatasetMeta] = {}
        self._total_rows = 0
        self._total_bytes = 0

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

    def create_dataset(
        self, source: str, interface: str, params: dict[str, Any], payload: dict[str, Any]
    ) -> DatasetMeta:
        rows = list(payload.get("rows") or [])
        encoded = json.dumps(rows, ensure_ascii=False, default=str).encode()
        if self._total_rows + len(rows) > self.limits.max_total_rows:
            raise ValueError("max_total_rows exceeded")
        if self._total_bytes + len(encoded) > self.limits.max_input_bytes:
            raise ValueError("max_input_bytes exceeded")
        dataset_id = secrets.token_urlsafe(18)
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / f"{dataset_id}.json").write_bytes(encoded)
        meta = DatasetMeta(
            dataset_id=dataset_id,
            source=source,
            interface=interface,
            params=params,
            columns=[str(value) for value in payload.get("columns") or []],
            returned=len(rows),
            total=int(payload.get("total") or len(rows)),
            truncated=bool(payload.get("truncated")),
            byte_size=len(encoded),
            sample=rows[:5],
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
