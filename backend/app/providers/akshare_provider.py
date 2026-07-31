"""AKShare data source: explorer + market + kline + quote + fund + limitup."""

from __future__ import annotations

from typing import Any

import akshare as ak

from .. import catalog
from .. import fund as fund_service
from .. import kline as kline_service
from .. import limitup as limitup_service
from .. import market as market_service
from .. import quote as quote_service
from ..serialize import normalize_result


class AkshareProvider:
    id = "akshare"
    label = "AKShare"
    features = ("explorer", "market", "kline", "quote", "fund", "limitup")
    docs_url = "https://akshare.akfamily.xyz/"
    def describe(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "features": list(self.features),
            "docs_url": self.docs_url,
            "ready": True,
            "message": None,
        }

    def health(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "ready": True,
            "version": getattr(ak, "__version__", "unknown"),
            "interface_count": len(catalog.build_catalog()),
            "features": list(self.features),
        }

    def get_categories(self) -> list[dict[str, Any]]:
        return catalog.get_categories()

    def list_interfaces(
        self, category: str | None = None, keyword: str | None = None
    ) -> list[dict[str, Any]]:
        return catalog.list_interfaces(category=category, keyword=keyword)

    def get_interface(self, name: str) -> dict[str, Any] | None:
        return catalog.get_interface(name)

    def fetch(
        self, name: str, params: dict[str, Any], limit: int
    ) -> dict[str, Any]:
        if not catalog.is_allowed(name):
            raise ValueError(f"Interface not allowed: {name}")
        try:
            func = getattr(ak, name)
        except AttributeError as exc:
            raise LookupError(str(exc)) from exc

        call_params: dict[str, Any] = {}
        for key, value in params.items():
            if value is None:
                continue
            call_params[key] = value

        try:
            result = func(**call_params) if call_params else func()
        except TypeError as exc:
            raise ValueError(f"参数错误: {exc}") from exc
        except Exception as exc:
            msg = f"{type(exc).__name__}: {exc}"
            lower = msg.lower()
            if "proxy" in lower:
                msg += (
                    "。本服务已尝试绕过失效本地代理；若仍失败，请检查系统代理"
                    "是否可用，或设置 AKSHARE_USE_SYSTEM_PROXY=1 后重启。"
                )
            if "length mismatch" in lower and "stock_hk_hot_rank" in name:
                msg += (
                    "。该接口需要港股 5 位代码（如 00700），不要填 A 股代码。"
                )
            elif "length mismatch" in lower:
                msg += "。通常表示上游返回空数据，或代码格式与接口要求不符。"
            raise RuntimeError(msg) from exc

        normalized = normalize_result(result, limit)
        return {"name": name, "params": call_params, **normalized}

    def get_kline(self, symbol: str, range_: str) -> dict[str, Any]:
        return kline_service.get_kline(symbol=symbol, range_=range_)

    def get_market(self) -> dict[str, Any]:
        return market_service.get_market()

    def get_quote(self, symbol: str, tick_limit: int = 40) -> dict[str, Any]:
        return quote_service.get_quote(symbol=symbol, tick_limit=tick_limit)

    def get_fund_search(self, q: str, limit: int = 20) -> dict[str, Any]:
        items = fund_service.search_funds(q, limit=limit)
        return {"source": self.id, "q": (q or "").strip(), "items": items}

    def get_fund_detail(self, symbol: str) -> dict[str, Any]:
        detail = fund_service.get_fund_detail(symbol)
        return {"source": self.id, **detail}

    def get_limit_up(self) -> dict[str, Any]:
        payload = limitup_service.get_limit_up()
        return {"source": self.id, **payload}
