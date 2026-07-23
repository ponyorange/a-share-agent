"""Fix broken local proxies and flaky East Money hosts for AKShare.

Problems commonly seen in Cursor / macOS environments:
1. HTTP(S)_PROXY points at a dead local port (e.g. 127.0.0.1:61126) → ProxyError
2. ``*.push2.eastmoney.com`` returns empty replies; ``*.push2delay.eastmoney.com`` works

Set AKSHARE_USE_SYSTEM_PROXY=1 to keep process proxy environment variables.
Set AKSHARE_DISABLE_HOST_REWRITE=1 to skip East Money host rewriting.
"""

from __future__ import annotations

import os
import re
from typing import Any
from urllib.parse import urlparse

_PROXY_KEYS = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "SOCKS_PROXY",
    "SOCKS5_PROXY",
    "socks_proxy",
    "socks5_proxy",
)

_APPLIED = False

# Numeric push2 nodes and bare push2 → push2delay (must run after push2his rule)
_PUSH2_RE = re.compile(
    r"https?://(?:\d+\.)?push2\.eastmoney\.com",
    flags=re.IGNORECASE,
)


def _truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _proxy_port_open(proxy_url: str, timeout: float = 0.35) -> bool:
    try:
        parsed = urlparse(proxy_url)
        host = parsed.hostname
        port = parsed.port
        if not host or not port:
            return False
        import socket

        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def clear_broken_proxies() -> None:
    """Drop env proxies that point at closed local ports; optionally clear all."""
    if _truthy("AKSHARE_USE_SYSTEM_PROXY"):
        # Still remove clearly dead local proxies
        for key in _PROXY_KEYS:
            value = os.environ.get(key)
            if not value:
                continue
            if "127.0.0.1" in value or "localhost" in value:
                if not _proxy_port_open(value):
                    os.environ.pop(key, None)
        return

    for key in _PROXY_KEYS:
        os.environ.pop(key, None)


def rewrite_eastmoney_url(url: str) -> str:
    """Rewrite unreachable East Money push2 hosts to push2delay mirrors."""
    if not isinstance(url, str):
        return url
    if "eastmoney.com" not in url.lower():
        return url
    # Prefer delay nodes that remain reachable on restricted networks
    return _PUSH2_RE.sub(
        lambda m: m.group(0).replace("push2.", "push2delay."),
        url,
    )


def _patch_requests() -> None:
    import requests

    original = requests.Session.request

    def patched(
        self: requests.Session,
        method: str,
        url: str,
        *args: Any,
        **kwargs: Any,
    ):
        # Ignore macOS/env proxies unless user explicitly opts in
        if not _truthy("AKSHARE_USE_SYSTEM_PROXY"):
            self.trust_env = False
        else:
            # Still avoid dead Cursor sandbox proxies via cleared env
            pass

        if isinstance(url, str) and not _truthy("AKSHARE_DISABLE_HOST_REWRITE"):
            url = rewrite_eastmoney_url(url)

        return original(self, method, url, *args, **kwargs)

    requests.Session.request = patched  # type: ignore[method-assign]


def _patch_getproxies() -> None:
    """Prevent urllib/requests from re-picking macOS system proxies."""
    if _truthy("AKSHARE_USE_SYSTEM_PROXY"):
        return
    import urllib.request

    urllib.request.getproxies = lambda: {}  # type: ignore[assignment]
    if hasattr(urllib.request, "getproxies_environment"):
        urllib.request.getproxies_environment = lambda: {}  # type: ignore[assignment]


def apply_network_fixes() -> dict[str, Any]:
    """Apply proxy + host fixes once. Safe to call repeatedly."""
    global _APPLIED
    if _APPLIED:
        return {"applied": True, "already": True}

    before = {k: os.environ.get(k) for k in _PROXY_KEYS if os.environ.get(k)}
    clear_broken_proxies()
    _patch_getproxies()
    _patch_requests()
    _APPLIED = True
    after = {k: os.environ.get(k) for k in _PROXY_KEYS if os.environ.get(k)}
    return {
        "applied": True,
        "already": False,
        "proxies_before": before,
        "proxies_after": after,
        "use_system_proxy": _truthy("AKSHARE_USE_SYSTEM_PROXY"),
        "host_rewrite": not _truthy("AKSHARE_DISABLE_HOST_REWRITE"),
    }
