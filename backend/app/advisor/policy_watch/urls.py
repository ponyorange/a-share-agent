"""Normalize article URLs and titles for dedup."""

from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

_DROP_QUERY = {"from", "spm", "ref"}


def normalize_url_key(url: str) -> str:
    raw = (url or "").strip()
    parsed = urlparse(raw)
    scheme = (parsed.scheme or "https").lower()
    host = (parsed.hostname or "").lower()
    port = parsed.port
    if port is not None and not (
        (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    ):
        netloc = f"{host}:{port}"
    else:
        netloc = host
    path = parsed.path or ""
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    kept: list[tuple[str, str]] = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        low = key.lower()
        if low.startswith("utm_") or low in _DROP_QUERY:
            continue
        kept.append((key, value))
    query = urlencode(kept, doseq=True)
    return urlunparse((scheme, netloc, path, "", query, ""))


def normalize_title(title: str) -> str:
    text = (title or "").replace("\u3000", " ")
    return " ".join(text.split()).casefold()


def titles_similar(a: str, b: str) -> bool:
    left = normalize_title(a)
    right = normalize_title(b)
    if not left or not right:
        return False
    if left == right:
        return True
    shorter, longer = (left, right) if len(left) <= len(right) else (right, left)
    return len(shorter) >= 8 and shorter in longer
