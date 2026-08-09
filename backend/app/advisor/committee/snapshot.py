"""Immutable, reproducible market snapshots built from injected collectors."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
import hashlib
import json
import math
from numbers import Integral, Real
import re
from typing import Any

from pydantic import Field, field_serializer, field_validator, model_validator

from .models import (
    CommitteeModel,
    Freshness,
    Horizon,
    NonEmptyStr,
    deep_freeze,
    deep_thaw,
    utc_now,
)


class SnapshotError(RuntimeError):
    pass


class CriticalDataError(SnapshotError):
    pass


class FutureDataError(SnapshotError):
    pass


Collector = Callable[..., Mapping[str, Any]]


@dataclass(frozen=True, slots=True)
class CollectorSpec:
    name: str
    source: str
    collector: Collector
    critical: bool = False


@dataclass(frozen=True, slots=True)
class _CanonicalDecimal:
    value: str


def _normalize(value: Any) -> Any:
    if isinstance(value, _CanonicalDecimal):
        return value
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, Integral):
        return int(value)
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("NaN and infinity are not snapshot-safe")
        if value == 0:
            canonical = "0"
        else:
            canonical = format(value, "f")
            if "." in canonical:
                canonical = canonical.rstrip("0").rstrip(".")
        return _CanonicalDecimal(canonical)
    if isinstance(value, Real):
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("NaN and infinity are not snapshot-safe")
        return 0.0 if number == 0 else number
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("snapshot datetimes must be timezone-aware")
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Enum):
        return _normalize(value.value)
    if isinstance(value, Mapping):
        return {
            str(key): _normalize(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (set, frozenset)):
        normalized = [_normalize(item) for item in value]
        return sorted(normalized, key=_canonical_json)
    if isinstance(value, (list, tuple)):
        return [_normalize(item) for item in value]
    if hasattr(value, "to_dict"):
        try:
            return _normalize(value.to_dict(orient="records"))
        except TypeError:
            return _normalize(value.to_dict())
    if hasattr(value, "model_dump"):
        return _normalize(value.model_dump(mode="json"))
    raise TypeError(f"unsupported snapshot value: {type(value).__name__}")


def _typed_ast(value: Any) -> Any:
    if isinstance(value, _CanonicalDecimal):
        return ["decimal", value.value]
    if value is None:
        return ["null"]
    if isinstance(value, bool):
        return ["bool", value]
    if isinstance(value, Enum):
        return ["enum", type(value).__qualname__, _typed_ast(value.value)]
    if isinstance(value, Integral):
        return ["int", str(int(value))]
    if isinstance(value, Real):
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("NaN and infinity are not snapshot-safe")
        return ["float", number.hex()]
    if isinstance(value, datetime):
        return ["datetime", _normalize(value)]
    if isinstance(value, date):
        return ["date", value.isoformat()]
    if isinstance(value, str):
        return ["string", len(value.encode("utf-8")), value]
    if isinstance(value, Mapping):
        pairs = [
            [_typed_ast(str(key)), _typed_ast(item)]
            for key, item in value.items()
        ]
        pairs.sort(
            key=lambda pair: json.dumps(
                pair[0],
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
        return ["map", pairs]
    if isinstance(value, (list, tuple)):
        return ["list", [_typed_ast(item) for item in value]]
    if isinstance(value, (set, frozenset)):
        items = [_typed_ast(item) for item in value]
        items.sort(
            key=lambda item: json.dumps(
                item,
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
        return ["set", items]
    raise TypeError(f"unsupported canonical value: {type(value).__name__}")


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        _typed_ast(value),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")


def _json_content(value: Any) -> Any:
    if isinstance(value, _CanonicalDecimal):
        return {"type": "decimal", "value": value.value}
    if isinstance(value, Mapping):
        return {str(key): _json_content(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_content(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted(
            [_json_content(item) for item in value],
            key=lambda item: json.dumps(
                item,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
        )
    return value


def _coerce_utc(value: Any, field_name: str) -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime):
        raise ValueError(f"{field_name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(timezone.utc)


_TEMPORAL_KEYS = frozenset(
    {
        "time",
        "date",
        "datetime",
        "timestamp",
        "as_of",
        "data_as_of",
        "trade_date",
        "published_at",
        "publish_time",
    }
)
_CAPTURE_KEYS = frozenset({"captured_at", "fetched_at", "collected_at"})


def _normalized_key(value: str) -> str:
    if re.search(r"[\u3400-\u9fff]", value):
        return re.sub(r"\s+", "", value).lower()
    snake = re.sub(r"(?<!^)(?=[A-Z])", "_", value).lower()
    return re.sub(r"[^a-z0-9]+", "_", snake).strip("_")


def _is_temporal_key(key: str) -> bool:
    if any(
        marker in key
        for marker in (
            "日期",
            "时间",
            "发布时间",
            "发布日期",
            "报告期",
            "统计期",
        )
    ):
        return True
    return (
        key in _TEMPORAL_KEYS
        or key.endswith("_date")
        or key.endswith("_time")
        or key.endswith("_timestamp")
        or key.endswith("_at")
    )


def _parse_observed_time(value: Any, field_name: str) -> datetime:
    if isinstance(value, datetime):
        return _coerce_utc(value, field_name)
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time(), timezone.utc)
    if isinstance(value, (Integral, Real)) and not isinstance(value, bool):
        epoch = float(value)
        if not math.isfinite(epoch):
            raise ValueError(f"{field_name} timestamp must be finite")
        if abs(epoch) >= 100_000_000_000:
            epoch /= 1000.0
        return datetime.fromtimestamp(epoch, tz=timezone.utc)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} is not a parseable timestamp")
    parsed = value.strip().replace("Z", "+00:00")
    parsed = (
        parsed.replace("年", "-")
        .replace("月", "-")
        .replace("日", "")
        .replace("时", ":")
        .replace("分", ":")
        .replace("秒", "")
    )
    if re.fullmatch(r"\d{8}", parsed):
        return datetime.strptime(parsed, "%Y%m%d").replace(tzinfo=timezone.utc)
    if len(parsed) == 10:
        return datetime.combine(
            date.fromisoformat(parsed), datetime.min.time(), timezone.utc
        )
    return _coerce_utc(datetime.fromisoformat(parsed), field_name)


def _assert_no_future(
    value: Any,
    *,
    as_of: datetime,
    collector_name: str,
    parent_key: str | None = None,
) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            key_text = _normalized_key(str(key))
            _assert_no_future(
                item,
                as_of=as_of,
                collector_name=collector_name,
                parent_key=key_text,
            )
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _assert_no_future(
                item,
                as_of=as_of,
                collector_name=collector_name,
                parent_key=parent_key,
            )
        return
    if parent_key is None or parent_key in _CAPTURE_KEYS:
        return
    if not _is_temporal_key(parent_key):
        return
    try:
        observed = _parse_observed_time(value, parent_key)
    except (OverflowError, OSError, TypeError, ValueError) as exc:
        raise FutureDataError(
            f"{collector_name} has invalid temporal field {parent_key}"
        ) from exc
    if observed > as_of:
        raise FutureDataError(
            f"{collector_name} contains data after the snapshot as_of"
        )


class SnapshotItem(CommitteeModel):
    name: NonEmptyStr
    source: NonEmptyStr
    critical: bool
    captured_at: datetime
    data_as_of: datetime | None = None
    freshness: Freshness = Freshness.FRESH
    degraded: bool = False
    error: str | None = Field(default=None, min_length=1, max_length=256)
    content: Any

    @field_validator("content", mode="before")
    @classmethod
    def normalize_and_freeze_content(cls, value: Any) -> Any:
        return deep_freeze(_normalize(value))

    @model_validator(mode="after")
    def validate_critical_quality(self) -> SnapshotItem:
        if self.critical and (
            self.freshness is not Freshness.FRESH
            or self.degraded
            or self.error is not None
        ):
            raise ValueError("critical snapshot item must be clean and fresh")
        return self

    @field_serializer("content")
    def serialize_content(self, value: Any) -> Any:
        return _json_content(deep_thaw(value))


class MarketSnapshot(CommitteeModel):
    snapshot_id: str = Field(min_length=64, max_length=64)
    as_of: datetime
    strategy_version: NonEmptyStr
    strategy_id: NonEmptyStr = "advisor-score-v2"
    horizon: Horizon
    universe: tuple[NonEmptyStr, ...]
    items: tuple[SnapshotItem, ...]
    created_at: datetime


class SnapshotBuilder:
    def __init__(
        self,
        collectors: tuple[CollectorSpec, ...] | list[CollectorSpec],
        *,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        names = [spec.name for spec in collectors]
        if len(names) != len(set(names)):
            raise ValueError("collector names must be unique")
        self._collectors = tuple(collectors)
        self._clock = clock

    def build(
        self,
        *,
        as_of: datetime,
        user_id: str | None = None,
        strategy_version: str,
        strategy_id: str = "advisor-score-v2",
        horizon: Horizon | str,
        universe: tuple[str, ...] | list[str],
    ) -> MarketSnapshot:
        as_of_utc = _coerce_utc(as_of, "as_of")
        resolved_horizon = Horizon(horizon)
        resolved_universe = tuple(sorted(set(universe)))
        if not resolved_universe:
            raise ValueError("universe cannot be empty")

        items = tuple(
            self._collect(
                spec,
                as_of=as_of_utc,
                user_id=user_id,
                strategy_version=strategy_version,
                horizon=resolved_horizon,
                universe=resolved_universe,
            )
            for spec in self._collectors
        )
        hash_input = {
            "as_of": as_of_utc,
            "strategy_version": strategy_version,
            "strategy_id": strategy_id,
            "horizon": resolved_horizon,
            "universe": resolved_universe,
            "items": [
                {
                    "name": item.name,
                    "source": item.source,
                    "critical": item.critical,
                    "data_as_of": item.data_as_of,
                    "freshness": item.freshness,
                    "degraded": item.degraded,
                    "error": item.error,
                    "content": deep_thaw(item.content),
                }
                for item in items
            ],
        }
        snapshot_id = hashlib.sha256(_canonical_json(hash_input)).hexdigest()
        return MarketSnapshot(
            snapshot_id=snapshot_id,
            as_of=as_of_utc,
            strategy_version=strategy_version,
            strategy_id=strategy_id,
            horizon=resolved_horizon,
            universe=resolved_universe,
            items=items,
            created_at=_coerce_utc(self._clock(), "clock"),
        )

    def _collect(
        self,
        spec: CollectorSpec,
        **context: Any,
    ) -> SnapshotItem:
        try:
            result = spec.collector(**context)
            if not isinstance(result, Mapping):
                raise TypeError("collector must return a mapping")
            captured_at = _coerce_utc(
                result.get("captured_at", self._clock()), "captured_at"
            )
            raw_data_as_of = result.get("data_as_of")
            data_as_of = (
                _coerce_utc(raw_data_as_of, "data_as_of")
                if raw_data_as_of is not None
                else None
            )
            if data_as_of is not None and data_as_of > context["as_of"]:
                raise FutureDataError(
                    f"{spec.name} contains data after the snapshot as_of"
                )
            raw_content = result.get("content")
            _assert_no_future(
                raw_content,
                as_of=context["as_of"],
                collector_name=spec.name,
            )
            content = deep_freeze(_normalize(raw_content))
            embedded_error = (
                raw_content.get("error")
                if isinstance(raw_content, Mapping)
                else None
            )
            embedded_errors = (
                raw_content.get("errors")
                if isinstance(raw_content, Mapping)
                else None
            )
            error = result.get("error")
            if error is None and embedded_error:
                error = "upstream_error"
            if error is None and embedded_errors:
                error = "upstream_errors"
            degraded = bool(result.get("degraded", False) or error)
            default_freshness = (
                Freshness.DEGRADED if degraded else Freshness.FRESH
            )
            freshness = Freshness(result.get("freshness", default_freshness))
            if spec.critical and (
                freshness is not Freshness.FRESH or degraded or error is not None
            ):
                raise CriticalDataError(
                    f"critical collector {spec.name} is not clean and fresh"
                )
            return SnapshotItem(
                name=spec.name,
                source=str(result.get("source") or spec.source),
                critical=spec.critical,
                captured_at=captured_at,
                data_as_of=data_as_of,
                freshness=freshness,
                degraded=degraded,
                error=str(error)[:256] if error else None,
                content=content,
            )
        except FutureDataError:
            raise
        except Exception as exc:
            if spec.critical:
                if isinstance(exc, CriticalDataError):
                    raise
                raise CriticalDataError(
                    f"critical collector {spec.name} failed: {type(exc).__name__}"
                ) from exc
            return SnapshotItem(
                name=spec.name,
                source=spec.source,
                critical=False,
                captured_at=_coerce_utc(self._clock(), "clock"),
                freshness=Freshness.ERROR,
                degraded=True,
                error=type(exc).__name__,
                content=deep_freeze({}),
            )


def _latest_observed_at(value: Any) -> datetime | None:
    observed: list[datetime] = []

    def visit(item: Any, parent_key: str | None = None) -> None:
        if isinstance(item, Mapping):
            for key, nested in item.items():
                visit(nested, _normalized_key(str(key)))
            return
        if isinstance(item, (list, tuple)):
            for nested in item:
                visit(nested, parent_key)
            return
        if (
            parent_key
            and parent_key not in _CAPTURE_KEYS
            and _is_temporal_key(parent_key)
        ):
            try:
                observed.append(_parse_observed_time(item, parent_key))
            except (OverflowError, OSError, TypeError, ValueError):
                pass

    visit(value)
    return max(observed) if observed else None


def _filter_content_as_of(value: Any, as_of: datetime) -> Any:
    if isinstance(value, list):
        filtered = []
        for item in value:
            if isinstance(item, Mapping):
                row_times = []
                for key, field_value in item.items():
                    normalized_key = _normalized_key(str(key))
                    if (
                        normalized_key not in _CAPTURE_KEYS
                        and _is_temporal_key(normalized_key)
                    ):
                        try:
                            row_times.append(
                                _parse_observed_time(
                                    field_value, normalized_key
                                )
                            )
                        except (
                            OverflowError,
                            OSError,
                            TypeError,
                            ValueError,
                        ):
                            continue
                if row_times and max(row_times) > as_of:
                    continue
            filtered.append(_filter_content_as_of(item, as_of))
        return filtered
    if isinstance(value, Mapping):
        return {
            key: _filter_content_as_of(item, as_of)
            for key, item in value.items()
        }
    return value


def _has_unknown_record_time(
    value: Any,
    *,
    inherited_time: bool = False,
) -> bool:
    if isinstance(value, Mapping):
        has_time = any(
            _is_temporal_key(_normalized_key(str(key)))
            for key in value
        )
        covered = inherited_time or has_time
        return any(
            _has_unknown_record_time(item, inherited_time=covered)
            for item in value.values()
            if isinstance(item, (Mapping, list, tuple))
        )
    if isinstance(value, (list, tuple)):
        mapping_items = [item for item in value if isinstance(item, Mapping)]
        if mapping_items and not inherited_time:
            for item in mapping_items:
                if not any(
                    _is_temporal_key(_normalized_key(str(key)))
                    for key in item
                ):
                    return True
        return any(
            _has_unknown_record_time(item, inherited_time=inherited_time)
            for item in value
        )
    return False


def _noncritical_result(
    content: Mapping[str, Any],
    *,
    as_of: datetime,
) -> dict[str, Any]:
    filtered = _filter_content_as_of(content, as_of)
    data_as_of = _latest_observed_at(filtered)
    errors = filtered.get("errors") or filtered.get("error")
    unknown_history = (
        data_as_of is None or _has_unknown_record_time(filtered)
    ) and as_of < utc_now()
    if unknown_history:
        filtered = {}
        data_as_of = None
    result: dict[str, Any] = {
        "content": filtered,
        "data_as_of": data_as_of,
    }
    if unknown_history:
        result.update(
            {
                "degraded": True,
                "freshness": Freshness.DEGRADED,
                "error": "unknown_data_as_of",
            }
        )
    elif errors:
        result.update(
            {
                "degraded": True,
                "freshness": Freshness.DEGRADED,
                "error": (
                    "upstream_errors"
                    if filtered.get("errors")
                    else "upstream_error"
                ),
            }
        )
    return result


def default_collector_specs(
    *,
    account_source: Callable[..., Mapping[str, Any]] | None = None,
) -> tuple[CollectorSpec, ...]:
    """Create lazy adapters around existing advisor data sources."""

    def kline(*, as_of: datetime, universe: tuple[str, ...], **_kwargs: Any):
        from ..features import fetch_daily_df

        content: dict[str, Any] = {}
        cutoff = as_of.date().isoformat()
        for symbol in universe:
            name, frame = fetch_daily_df(symbol)
            work = frame[frame["time"].astype(str).str.slice(0, 10) <= cutoff]
            if work.empty:
                raise RuntimeError(f"no eligible kline for {symbol}")
            content[symbol] = {
                "name": name,
                "bars": work.to_dict(orient="records"),
            }
        return {"content": content, "data_as_of": _latest_observed_at(content)}

    def market(*, as_of: datetime, **_kwargs: Any):
        from ..market_context import get_market_score

        content = get_market_score(as_of.date().isoformat())
        return {"content": content, "data_as_of": _latest_observed_at(content)}

    def news(*, as_of: datetime, **_kwargs: Any):
        from ..agent.unstructured import fetch_market_cctv_news

        content = fetch_market_cctv_news(as_of.strftime("%Y%m%d"))
        return _noncritical_result(content, as_of=as_of)

    def fundamentals(*, as_of: datetime, **_kwargs: Any):
        from ..agent.unstructured import fetch_macro_china_snapshot

        content = fetch_macro_china_snapshot()
        return _noncritical_result(content, as_of=as_of)

    def signal_graph(*, as_of: datetime, universe: tuple[str, ...], **_kwargs: Any):
        from ..signal_graph.service import generate_signal, signal_graph_config

        cfg = signal_graph_config()
        if not cfg.get("enabled"):
            return _noncritical_result(
                {"enabled": False, "items": []},
                as_of=as_of,
            )
        day = as_of.date().isoformat()
        items: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        for symbol in universe:
            try:
                payload = generate_signal(symbol, trade_date=day, persist=True)
                items.append(
                    {
                        "symbol": payload.get("symbol"),
                        "action": payload.get("action"),
                        "scores": payload.get("scores"),
                        "margin": payload.get("margin"),
                        "prediction_id": payload.get("prediction_id"),
                        "blocked_reason": payload.get("blocked_reason"),
                        "market_regime": payload.get("market_regime"),
                        "patterns": payload.get("patterns"),
                    }
                )
            except Exception as exc:
                errors.append({"symbol": symbol, "error": str(exc)})
        content = {
            "enabled": True,
            "trade_date": day,
            "horizon_days": cfg.get("horizon_days"),
            "items": items,
            "errors": errors,
        }
        return _noncritical_result(content, as_of=as_of)

    def portfolio_account(
        *,
        as_of: datetime,
        user_id: str | None,
        **_kwargs: Any,
    ):
        if not user_id:
            raise ValueError("account collector requires user_id")
        if account_source is not None:
            content = account_source(user_id=user_id, as_of=as_of)
        else:
            from .dependencies import _portfolio_worker

            raw = _portfolio_worker(user_id, as_of.isoformat())
            content = {
                "cash": float(raw["cash"]),
                "equity": float(raw["equity"]),
                "positions": list(raw["positions"].values()),
                "version": raw["version"],
                "account_version": raw["account_version"],
                "data_as_of": raw["data_as_of"],
            }
        if not isinstance(content, Mapping):
            raise TypeError("account source must return a mapping")
        required = {"cash", "equity", "positions"}
        if not required <= set(content):
            raise ValueError("account source is incomplete")
        content = dict(content)
        data_as_of = content.pop("data_as_of", None)
        if data_as_of is None:
            raise ValueError("account source must expose true data_as_of")
        if not content.get("version"):
            raise ValueError("account source must expose version")
        return {
            "content": content,
            "data_as_of": data_as_of,
            "source": (
                "injected.account_source"
                if account_source is not None
                else "advisor.paper.get_account"
            ),
        }

    def trading_calendar(*, as_of: datetime, **_kwargs: Any):
        from .dependencies import _trade_calendar_worker

        raw = _trade_calendar_worker(as_of.isoformat())
        return {
            "content": {
                "sessions": list(raw["sessions"]),
                "source": raw["source"],
            },
            "data_as_of": as_of,
            "source": raw["source"],
        }

    return (
        CollectorSpec("kline", "advisor.features", kline, critical=True),
        CollectorSpec("market", "advisor.market_context", market, critical=True),
        CollectorSpec(
            "trading_calendar",
            "AKShare.tool_trade_date_hist_sina",
            trading_calendar,
            critical=True,
        ),
        CollectorSpec("news", "advisor.agent.unstructured", news),
        CollectorSpec(
            "fundamentals", "advisor.agent.unstructured", fundamentals
        ),
        CollectorSpec(
            "signal_graph",
            "advisor.signal_graph",
            signal_graph,
        ),
        CollectorSpec(
            "portfolio_account",
            "advisor.paper.get_account",
            portfolio_account,
            critical=True,
        ),
    )
