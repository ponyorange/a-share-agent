"""BaoStock data source — explorer + kline (http://baostock.com)."""

from __future__ import annotations

from typing import Any

from . import baostock_catalog
from . import baostock_kline
from .baostock_common import result_to_df, session
from ..serialize import normalize_result


class BaostockProvider:
    id = "baostock"
    label = "BaoStock"
    features = ("explorer", "kline")
    docs_url = "http://baostock.com/baostock/index.php"

    def describe(self) -> dict[str, Any]:
        ready = True
        message = None
        try:
            import baostock  # noqa: F401
        except ImportError:
            ready = False
            message = "未安装 baostock，请 pip install baostock"
        return {
            "id": self.id,
            "label": self.label,
            "features": list(self.features),
            "docs_url": self.docs_url,
            "ready": ready,
            "message": message,
        }

    def health(self) -> dict[str, Any]:
        version = "unknown"
        ready = False
        message = None
        try:
            import baostock as bs

            version = getattr(bs, "__version__", "installed")
            ready = True
        except ImportError:
            version = "not_installed"
            message = "未安装 baostock"
        return {
            "id": self.id,
            "label": self.label,
            "ready": ready,
            "version": version,
            "interface_count": len(baostock_catalog.build_catalog()),
            "features": list(self.features),
            "message": message,
        }

    def get_categories(self) -> list[dict[str, Any]]:
        catalog = baostock_catalog.build_catalog()
        counts: dict[str, int] = {}
        for item in catalog:
            counts[item["category"]] = counts.get(item["category"], 0) + 1
        result = []
        for key in baostock_catalog.CATEGORY_LABELS:
            if key in counts:
                result.append(
                    {
                        "id": key,
                        "label": baostock_catalog.CATEGORY_LABELS[key],
                        "count": counts[key],
                    }
                )
        for key, count in sorted(counts.items()):
            if key not in baostock_catalog.CATEGORY_LABELS:
                result.append({"id": key, "label": key, "count": count})
        return result

    def list_interfaces(
        self, category: str | None = None, keyword: str | None = None
    ) -> list[dict[str, Any]]:
        catalog = baostock_catalog.build_catalog()
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

    def get_interface(self, name: str) -> dict[str, Any] | None:
        for item in baostock_catalog.build_catalog():
            if item["name"] == name:
                return item
        return None

    def fetch(
        self, name: str, params: dict[str, Any], limit: int
    ) -> dict[str, Any]:
        item = self.get_interface(name)
        if not item:
            raise ValueError(f"Interface not allowed: {name}")

        call_params: dict[str, Any] = {}
        for key, value in params.items():
            if value is None:
                continue
            if isinstance(value, str) and value.strip() == "":
                continue
            call_params[key] = value

        for key in ("year", "quarter"):
            if key in call_params and not isinstance(call_params[key], int):
                try:
                    call_params[key] = int(call_params[key])
                except (TypeError, ValueError):
                    pass

        try:
            with session() as bs:
                func = getattr(bs, name, None)
                if func is None:
                    raise LookupError(f"BaoStock 无此接口: {name}")
                rs = func(**call_params) if call_params else func()
                df = result_to_df(rs)
        except (ValueError, LookupError, RuntimeError):
            raise
        except Exception as exc:
            raise RuntimeError(f"{type(exc).__name__}: {exc}") from exc

        normalized = normalize_result(df, limit)
        return {"name": name, "params": call_params, **normalized}

    def get_kline(self, symbol: str, range_: str) -> dict[str, Any]:
        return baostock_kline.get_kline(symbol=symbol, range_=range_)
