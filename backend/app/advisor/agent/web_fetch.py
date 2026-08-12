"""SSRF-safe URL fetch for agent fetch_url tool."""

from __future__ import annotations

import ipaddress
import re
import socket
from typing import Any
from urllib.parse import urlparse

import httpx

from .web_limits import get_agent_web_config

_SCRIPT_RE = re.compile(r"<script\b[^>]*>.*?</script>", re.I | re.S)
_STYLE_RE = re.compile(r"<style\b[^>]*>.*?</style>", re.I | re.S)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")

_ALLOWED_CONTENT_HINTS = (
    "text/",
    "application/json",
    "application/xml",
    "application/xhtml",
    "application/javascript",
)


def _host_ips(hostname: str) -> list[str]:
    infos = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    ips: list[str] = []
    for info in infos:
        addr = info[4][0]
        if addr not in ips:
            ips.append(addr)
    return ips


def _ip_blocked(ip_str: str) -> bool:
    ip = ipaddress.ip_address(ip_str)
    return bool(
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def is_url_safe_for_fetch(url: str, *, allowed_ports: list[int]) -> tuple[bool, str]:
    raw = (url or "").strip()
    if not raw:
        return False, "禁止：URL 为空"
    parsed = urlparse(raw)
    if parsed.scheme not in ("http", "https"):
        return False, "禁止：仅允许 http/https"
    host = parsed.hostname
    if not host:
        return False, "禁止：缺少主机名"
    port = parsed.port
    if port is None:
        port = 443 if parsed.scheme == "https" else 80
    if int(port) not in {int(p) for p in allowed_ports}:
        return False, "禁止：端口不在允许列表"
    try:
        ips = _host_ips(host)
    except OSError:
        return False, "禁止：无法解析主机名"
    if not ips:
        return False, "禁止：无法解析主机名"
    for ip in ips:
        if _ip_blocked(ip):
            return False, "禁止：目标为内网或本机地址"
    return True, ""


def html_to_text(html: str, *, max_chars: int) -> str:
    text = _SCRIPT_RE.sub(" ", html)
    text = _STYLE_RE.sub(" ", text)
    text = _TAG_RE.sub(" ", text)
    text = _WS_RE.sub(" ", text).strip()
    if len(text) > max_chars:
        text = text[:max_chars]
    return text


_html_to_text = html_to_text


def _content_type_ok(content_type: str | None) -> bool:
    if not content_type:
        return True
    ct = content_type.split(";")[0].strip().lower()
    return any(ct.startswith(h) for h in _ALLOWED_CONTENT_HINTS)


def fetch_url_text(url: str, *, cfg: dict[str, Any] | None = None) -> str:
    section = cfg if cfg is not None else get_agent_web_config().get("fetch_url") or {}
    allowed_ports = [int(p) for p in (section.get("allowed_ports") or [80, 443])]
    timeout = float(section.get("timeout_seconds") or 20)
    max_bytes = int(section.get("max_bytes") or 524288)
    max_text = int(section.get("max_text_chars") or 80000)
    max_redirects = int(section.get("max_redirects") or 3)

    current = (url or "").strip()
    try:
        with httpx.Client(timeout=timeout, follow_redirects=False) as client:
            for _ in range(max_redirects + 1):
                ok, reason = is_url_safe_for_fetch(current, allowed_ports=allowed_ports)
                if not ok:
                    return f"错误：{reason}"
                response = client.get(current, headers={"User-Agent": "share-data-agent/1.0"})
                if response.is_redirect:
                    loc = response.headers.get("location")
                    if not loc:
                        return "错误：重定向缺少 Location"
                    current = str(httpx.URL(current).join(loc))
                    continue
                if response.status_code >= 400:
                    return f"错误：HTTP {response.status_code}"
                if not _content_type_ok(response.headers.get("content-type")):
                    return "错误：不支持的 Content-Type"
                data = response.content[: max_bytes + 1]
                if len(data) > max_bytes:
                    data = data[:max_bytes]
                text = data.decode(response.encoding or "utf-8", errors="replace")
                return _html_to_text(text, max_chars=max_text)
        return "错误：重定向次数过多"
    except Exception as exc:  # noqa: BLE001
        return f"错误：抓取失败: {type(exc).__name__}"
