"""Tushare Pro data source — explorer first (https://tushare.pro/document/2)."""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

from . import tushare_catalog
from ..serialize import normalize_result


def _token() -> str | None:
    return (
        os.environ.get("TUSHARE_TOKEN")
        or os.environ.get("TUSHARE_PRO_TOKEN")
        or os.environ.get("TS_TOKEN")
        or None
    )


@lru_cache(maxsize=1)
def _pro_api():
    import tushare as ts

    token = _token()
    if not token:
        raise RuntimeError(
            "未配置 Tushare Token。请设置环境变量 TUSHARE_TOKEN 后重启后端。"
            "申请地址：https://tushare.pro/register"
        )
    return ts.pro_api(token)


class TushareProvider:
    id = "tushare"
    label = "Tushare"
    features = ("explorer",)
    docs_url = "https://tushare.pro/document/2"

    def describe(self) -> dict[str, Any]:
        token_ok = bool(_token())
        return {
            "id": self.id,
            "label": self.label,
            "features": list(self.features),
            "docs_url": self.docs_url,
            "ready": token_ok,
            "message": None
            if token_ok
            else "未配置 TUSHARE_TOKEN，接口浏览器可浏览目录，调用需先配置 Token",
        }

    def health(self) -> dict[str, Any]:
        token_ok = bool(_token())
        version = "unknown"
        try:
            import tushare as ts

            version = getattr(ts, "__version__", "unknown")
        except Exception:
            version = "not_installed"
        return {
            "id": self.id,
            "label": self.label,
            "ready": token_ok,
            "version": version,
            "token_configured": token_ok,
            "interface_count": len(tushare_catalog.build_catalog()),
            "features": list(self.features),
            "message": self.describe()["message"],
        }

    def get_categories(self) -> list[dict[str, Any]]:
        catalog = tushare_catalog.build_catalog()
        counts: dict[str, int] = {}
        for item in catalog:
            counts[item["category"]] = counts.get(item["category"], 0) + 1
        result = []
        for key in tushare_catalog.CATEGORY_LABELS:
            if key in counts:
                result.append(
                    {
                        "id": key,
                        "label": tushare_catalog.CATEGORY_LABELS[key],
                        "count": counts[key],
                    }
                )
        for key, count in sorted(counts.items()):
            if key not in tushare_catalog.CATEGORY_LABELS:
                result.append({"id": key, "label": key, "count": count})
        return result

    def list_interfaces(
        self, category: str | None = None, keyword: str | None = None
    ) -> list[dict[str, Any]]:
        catalog = tushare_catalog.build_catalog()
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
        for item in tushare_catalog.build_catalog():
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

        try:
            if name == "pro_bar":
                import tushare as ts

                token = _token()
                if not token:
                    raise RuntimeError(
                        "未配置 TUSHARE_TOKEN。请设置环境变量后重启后端。"
                    )
                # pro_bar is a module-level helper, not pro_api method
                result = ts.pro_bar(token=token, **call_params)
            else:
                pro = _pro_api()
                func = getattr(pro, name, None)
                if func is None:
                    result = pro.query(name, **call_params)
                else:
                    result = func(**call_params)
        except RuntimeError:
            raise
        except Exception as exc:
            msg = f"{type(exc).__name__}: {exc}"
            if "token" in msg.lower() or "权限" in msg:
                msg += "。请确认 TUSHARE_TOKEN 有效，且积分/权限覆盖该接口。"
            raise RuntimeError(msg) from exc

        normalized = normalize_result(result, limit)
        return {"name": name, "params": call_params, **normalized}
