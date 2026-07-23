"""FastAPI application: multi-source market data explorer."""
# ruff: noqa: E402

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from .env import load_env
from .proxy_fix import apply_network_fixes

load_env()
apply_network_fixes()

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import providers
from .advisor import router as advisor_router
from .auth import _required_secret, router as auth_router, seed_dev_user
from .db import ensure_indexes, get_client, ping as mongo_ping
from .providers.akshare_provider import AkshareProvider


def _cors_origins() -> list[str]:
    raw = (os.getenv("CORS_ORIGINS") or "").strip()
    if raw == "*":
        return ["*"]
    if raw:
        return [o.strip() for o in raw.split(",") if o.strip()]
    # 本地开发默认；Docker 部署请在环境变量中设置 CORS_ORIGINS
    return [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5174",
    ]


def _validate_startup_configuration() -> None:
    get_client()
    _required_secret("JWT_SECRET")


def _initialize() -> None:
    _validate_startup_configuration()
    ensure_indexes()
    seed_dev_user()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    _initialize()
    yield


app = FastAPI(title="Share Data Explorer", version="2.1.0", lifespan=lifespan)

_origins = _cors_origins()
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=_origins != ["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(advisor_router)


class FetchRequest(BaseModel):
    name: str = Field(..., description="Interface / API name")
    params: dict[str, Any] = Field(default_factory=dict)
    limit: int = Field(default=500, ge=1, le=5000)


def _provider_or_404(source: str):
    try:
        return providers.get_provider(source)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/health")
def health() -> dict[str, Any]:
    sources = [providers.get_provider(s["id"]).health() for s in providers.list_sources()]
    mongo: dict[str, Any]
    try:
        mongo = mongo_ping()
    except Exception as exc:
        mongo = {"ok": False, "error": type(exc).__name__}
    return {
        "status": "ok",
        "network": apply_network_fixes(),
        "sources": sources,
        "mongo": mongo,
    }


@app.get("/api/sources")
def list_sources() -> dict[str, Any]:
    return {"sources": providers.list_sources()}


@app.get("/api/{source}/health")
def source_health(source: str) -> dict[str, Any]:
    return _provider_or_404(source).health()


@app.get("/api/{source}/categories")
def categories(source: str) -> dict[str, Any]:
    cats = _provider_or_404(source).get_categories()
    return {"source": source, "categories": cats, "total": sum(c["count"] for c in cats)}


@app.get("/api/{source}/interfaces")
def interfaces(
    source: str,
    category: str | None = Query(default=None),
    keyword: str | None = Query(default=None),
) -> dict[str, Any]:
    items = _provider_or_404(source).list_interfaces(
        category=category, keyword=keyword
    )
    return {"source": source, "interfaces": items, "count": len(items)}


@app.get("/api/{source}/interfaces/{name}")
def interface_detail(source: str, name: str) -> dict[str, Any]:
    item = _provider_or_404(source).get_interface(name)
    if not item:
        raise HTTPException(status_code=404, detail=f"Interface not found: {name}")
    return {"source": source, **item}


@app.post("/api/{source}/fetch")
def fetch_data(source: str, body: FetchRequest) -> dict[str, Any]:
    provider = _provider_or_404(source)
    try:
        result = provider.fetch(body.name, body.params, body.limit)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"调用失败: {type(exc).__name__}"
        ) from exc
    return {"source": source, **result}


@app.get("/api/{source}/kline")
def kline(
    source: str,
    symbol: str = Query(..., description="A股6位代码，如 000001"),
    range: str = Query(default="daily", description="realtime|5d|daily|weekly|monthly"),
) -> dict[str, Any]:
    provider = _provider_or_404(source)
    if "kline" not in provider.features:
        raise HTTPException(
            status_code=404,
            detail=f"数据源 {source} 暂不支持 K 线（features={list(provider.features)}）",
        )
    get_kline = getattr(provider, "get_kline", None)
    if get_kline is None:
        raise HTTPException(status_code=404, detail="K 线未实现")
    try:
        return get_kline(symbol=symbol, range_=range)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"K线获取失败: {type(exc).__name__}"
        ) from exc


@app.get("/api/{source}/market")
def market(source: str) -> dict[str, Any]:
    provider = _provider_or_404(source)
    if "market" not in provider.features:
        raise HTTPException(
            status_code=404,
            detail=f"数据源 {source} 暂不支持大盘行情（features={list(provider.features)}）",
        )
    get_market = getattr(provider, "get_market", None)
    if get_market is None:
        raise HTTPException(status_code=404, detail="大盘行情未实现")
    try:
        return get_market()
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"大盘行情获取失败: {type(exc).__name__}"
        ) from exc


@app.get("/api/{source}/quote")
def quote(
    source: str,
    symbol: str = Query(..., description="A股6位代码，如 600519"),
    tick_limit: int = Query(default=40, ge=10, le=100),
) -> dict[str, Any]:
    provider = _provider_or_404(source)
    if "quote" not in provider.features:
        raise HTTPException(
            status_code=404,
            detail=f"数据源 {source} 暂不支持盘口（features={list(provider.features)}）",
        )
    get_quote = getattr(provider, "get_quote", None)
    if get_quote is None:
        raise HTTPException(status_code=404, detail="盘口未实现")
    try:
        return get_quote(symbol=symbol, tick_limit=tick_limit)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"盘口获取失败: {type(exc).__name__}"
        ) from exc


# —— Backward-compatible aliases → akshare ——
@app.get("/api/categories")
def categories_legacy() -> dict[str, Any]:
    return categories(AkshareProvider.id)


@app.get("/api/interfaces")
def interfaces_legacy(
    category: str | None = Query(default=None),
    keyword: str | None = Query(default=None),
) -> dict[str, Any]:
    return interfaces(AkshareProvider.id, category=category, keyword=keyword)


@app.get("/api/interfaces/{name}")
def interface_detail_legacy(name: str) -> dict[str, Any]:
    return interface_detail(AkshareProvider.id, name)


@app.post("/api/fetch")
def fetch_legacy(body: FetchRequest) -> dict[str, Any]:
    return fetch_data(AkshareProvider.id, body)


@app.get("/api/kline")
def kline_legacy(
    symbol: str = Query(...),
    range: str = Query(default="daily"),
) -> dict[str, Any]:
    return kline(AkshareProvider.id, symbol=symbol, range=range)


@app.get("/api/market")
def market_legacy() -> dict[str, Any]:
    return market(AkshareProvider.id)


def _mount_static() -> None:
    """Docker / 生产：托管打包后的前端（顾问 /、数据后台 /explorer/）。

    注意：Starlette 会先匹配普通路由再匹配 Mount。
    因此不能用 ``/{full_path}`` 通配后再依赖 Mount(/explorer)，
    否则 /explorer/* 会被通配截获并 404。
    """
    root = Path(os.getenv("STATIC_ROOT") or "").expanduser()
    if not root.is_dir():
        return

    explorer = root / "explorer"
    advisor = root / "advisor"

    if explorer.is_dir():
        explorer_assets = explorer / "assets"
        if explorer_assets.is_dir():
            app.mount(
                "/explorer/assets",
                StaticFiles(directory=str(explorer_assets)),
                name="explorer-assets",
            )

        @app.get("/explorer")
        @app.get("/explorer/")
        def explorer_index() -> FileResponse:
            return FileResponse(explorer / "index.html")

        @app.get("/explorer/{full_path:path}")
        def explorer_spa(full_path: str) -> FileResponse:
            candidate = explorer / full_path
            if candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(explorer / "index.html")

    if not advisor.is_dir():
        return

    assets = advisor / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=str(assets)), name="advisor-assets")

    @app.get("/")
    def advisor_index() -> FileResponse:
        return FileResponse(advisor / "index.html")

    @app.get("/{full_path:path}")
    def advisor_spa(full_path: str) -> FileResponse:
        if full_path == "api" or full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not Found")
        # explorer 已由上方专用路由处理；此处再兜一层避免误伤
        if full_path == "explorer" or full_path.startswith("explorer/"):
            if explorer.is_dir():
                rel = full_path[len("explorer") :].lstrip("/")
                if rel:
                    candidate = explorer / rel
                    if candidate.is_file():
                        return FileResponse(candidate)
                return FileResponse(explorer / "index.html")
            raise HTTPException(status_code=404, detail="Not Found")
        candidate = advisor / full_path
        if candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(advisor / "index.html")


_mount_static()
