import json

import httpx
import pytest

from app.advisor.agent import web_tavily


def test_tavily_search_json(monkeypatch):
    monkeypatch.setattr(
        web_tavily,
        "_request_search",
        lambda api_key, payload, timeout: {
            "results": [
                {
                    "title": "A",
                    "url": "https://example.com/a",
                    "content": "hi",
                    "score": 0.9,
                }
            ]
        },
    )
    out = json.loads(web_tavily.tavily_search("k", "q", max_results=3))
    assert out[0]["url"] == "https://example.com/a"


def test_validate_tavily_key_raises(monkeypatch):
    def boom(*a, **k):
        raise httpx.HTTPStatusError(
            "bad",
            request=httpx.Request("POST", "https://api.tavily.com/search"),
            response=httpx.Response(401),
        )

    monkeypatch.setattr(web_tavily, "_request_search", boom)
    with pytest.raises(ValueError):
        web_tavily.validate_tavily_key("bad")


def test_validate_tavily_key_ok(monkeypatch):
    monkeypatch.setattr(
        web_tavily,
        "_request_search",
        lambda api_key, payload, timeout: {"results": []},
    )
    web_tavily.validate_tavily_key("tvly-test")
