"""Escalation helpers and pipeline for agent fetch_url (httpx → Scrapling → stealth)."""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from typing import Any, Literal

from .web_fetch import fetch_url_text, html_to_text, is_url_safe_for_fetch
from .web_limits import get_agent_web_config

FetchVia = Literal["httpx", "scrapling", "stealth"]
LevelFn = Callable[[str], str]
LevelCb = Callable[[FetchVia], None]

DEFAULT_BLOCK_PATTERNS: list[str] = [
    "just a moment",
    "cf-browser-verification",
    "attention required",
    "access denied",
    "verify you are human",
    "checking your browser",
]

_DEFAULT_ESCALATION: dict[str, Any] = {
    "enabled": True,
    "min_text_chars": 200,
    "max_total_seconds": 90,
    "l2_timeout_seconds": 30,
    "l3_timeout_seconds": 60,
    "enable_stealth": True,
    "solve_cloudflare": True,
    "headless": True,
    "block_patterns": list(DEFAULT_BLOCK_PATTERNS),
}

_FETCH_VIA_RE = re.compile(r"^# fetch_via: (httpx|scrapling|stealth)\n")


def get_escalation_config(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    section = cfg if cfg is not None else (get_agent_web_config().get("fetch_url") or {})
    raw = section.get("escalation") if isinstance(section, dict) else None
    out = dict(_DEFAULT_ESCALATION)
    if isinstance(raw, dict):
        out.update(raw)
    patterns = out.get("block_patterns")
    if not isinstance(patterns, list) or not patterns:
        out["block_patterns"] = list(DEFAULT_BLOCK_PATTERNS)
    else:
        out["block_patterns"] = [str(p) for p in patterns]
    return out


def is_ssrf_or_policy_error(text: str) -> bool:
    t = (text or "").strip()
    if not t.startswith("错误："):
        return False
    if t.startswith("错误：禁止"):
        return True
    body = t[3:]
    markers = ("内网", "本机", "仅允许 http", "端口不在允许", "缺少主机名", "无法解析", "URL 为空")
    return any(m in body for m in markers)


def strip_fetch_via_meta(text: str) -> str:
    return _FETCH_VIA_RE.sub("", text or "", count=1)


def with_fetch_via(text: str, via: FetchVia) -> str:
    body = strip_fetch_via_meta(text or "")
    return f"# fetch_via: {via}\n{body}"


def needs_escalation(
    text: str,
    *,
    min_text_chars: int,
    block_patterns: list[str],
) -> bool:
    if is_ssrf_or_policy_error(text):
        return False
    raw = text or ""
    if raw.startswith("错误："):
        return True
    body = strip_fetch_via_meta(raw).strip()
    if len(body) < int(min_text_chars):
        return True
    lower = body.lower()
    for pat in block_patterns:
        if pat and pat.lower() in lower:
            return True
    return False


def _response_to_html(page: Any) -> str:
    for attr in ("html_content", "body", "html"):
        val = getattr(page, attr, None)
        if val is None:
            continue
        if isinstance(val, bytes):
            return val.decode("utf-8", errors="replace")
        return str(val)
    getter = getattr(page, "get_all_text", None)
    if callable(getter):
        try:
            return str(getter())
        except Exception:  # noqa: BLE001
            pass
    return str(page)


def scrapling_http_fetch(
    url: str,
    *,
    timeout: float,
    max_text_chars: int,
) -> str:
    try:
        from scrapling.fetchers import Fetcher
    except ImportError:
        return "错误：增强抓取不可用（未安装 scrapling）"
    try:
        # timeout may be ignored by some scrapling versions; keep signature stable
        _ = timeout
        page = Fetcher.get(url)
        return html_to_text(_response_to_html(page), max_chars=max_text_chars)
    except Exception as exc:  # noqa: BLE001
        return f"错误：scrapling 抓取失败: {type(exc).__name__}"


def scrapling_stealth_fetch(
    url: str,
    *,
    timeout: float,
    max_text_chars: int,
    headless: bool,
    solve_cloudflare: bool,
) -> str:
    try:
        from scrapling.fetchers import StealthyFetcher
    except ImportError:
        return "错误：增强抓取不可用（未安装 scrapling）"
    try:
        _ = timeout
        page = StealthyFetcher.fetch(
            url,
            headless=headless,
            solve_cloudflare=solve_cloudflare,
            network_idle=True,
        )
        return html_to_text(_response_to_html(page), max_chars=max_text_chars)
    except Exception as exc:  # noqa: BLE001
        return f"错误：stealth 抓取失败: {type(exc).__name__}"


def fetch_url_with_escalation(
    url: str,
    *,
    cfg: dict[str, Any] | None = None,
    l1: LevelFn | None = None,
    l2: LevelFn | None = None,
    l3: LevelFn | None = None,
    on_level: LevelCb | None = None,
) -> str:
    section = cfg if cfg is not None else (get_agent_web_config().get("fetch_url") or {})
    allowed_ports = [int(p) for p in (section.get("allowed_ports") or [80, 443])]
    max_text = int(section.get("max_text_chars") or 80000)
    esc = get_escalation_config(section)

    ok, reason = is_url_safe_for_fetch((url or "").strip(), allowed_ports=allowed_ports)
    if not ok:
        return f"错误：{reason}"

    def _l1(u: str) -> str:
        if l1 is not None:
            return l1(u)
        return fetch_url_text(u, cfg=section)

    def _l2(u: str) -> str:
        if l2 is not None:
            return l2(u)
        return scrapling_http_fetch(
            u,
            timeout=float(esc.get("l2_timeout_seconds") or 30),
            max_text_chars=max_text,
        )

    def _l3(u: str) -> str:
        if l3 is not None:
            return l3(u)
        return scrapling_stealth_fetch(
            u,
            timeout=float(esc.get("l3_timeout_seconds") or 60),
            max_text_chars=max_text,
            headless=bool(esc.get("headless", True)),
            solve_cloudflare=bool(esc.get("solve_cloudflare", True)),
        )

    levels: list[tuple[FetchVia, LevelFn]] = [("httpx", _l1)]
    if esc.get("enabled", True):
        levels.append(("scrapling", _l2))
        if esc.get("enable_stealth", True):
            levels.append(("stealth", _l3))

    started = time.monotonic()
    max_total = float(esc.get("max_total_seconds") or 90)
    min_chars = int(esc.get("min_text_chars") or 200)
    patterns = [str(p) for p in (esc.get("block_patterns") or DEFAULT_BLOCK_PATTERNS)]

    attempted: list[str] = []
    last_error = "未知错误"
    last_body = ""

    for via, fn in levels:
        if time.monotonic() - started > max_total:
            if last_body and not last_body.startswith("错误："):
                return with_fetch_via(last_body, attempted[-1] if attempted else "httpx")  # type: ignore[arg-type]
            return f"错误：抓取失败（已尝试 {'/'.join(attempted) or 'none'}）: 超时"

        if on_level is not None:
            on_level(via)
        attempted.append(via)
        try:
            result = fn(url)
        except Exception as exc:  # noqa: BLE001
            result = f"错误：抓取失败: {type(exc).__name__}"

        if is_ssrf_or_policy_error(result):
            return result

        if not result.startswith("错误："):
            last_body = strip_fetch_via_meta(result)
            if not needs_escalation(
                last_body, min_text_chars=min_chars, block_patterns=patterns
            ):
                return with_fetch_via(last_body, via)
            # escalation off: return L1 even if short/shell
            if not esc.get("enabled", True):
                return with_fetch_via(last_body, via)
            last_error = "正文过短或疑似拦截页"
        else:
            last_error = result
            # missing scrapling / browser: skip without treating as hard stop
            if "增强抓取不可用" in result or "未安装 scrapling" in result:
                continue

        if via == levels[-1][0]:
            break

    if last_body and not needs_escalation(
        last_body, min_text_chars=min_chars, block_patterns=patterns
    ):
        return with_fetch_via(last_body, attempted[-1])  # type: ignore[arg-type]
    if last_body and not last_body.startswith("错误："):
        return (
            f"错误：抓取失败（已尝试 {'/'.join(attempted)}）: "
            f"正文过短或疑似拦截页"
        )
    return f"错误：抓取失败（已尝试 {'/'.join(attempted)}）: {last_error}"
