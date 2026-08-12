from app.advisor.agent.web_fetch_escalation import (
    fetch_url_with_escalation,
    is_ssrf_or_policy_error,
    needs_escalation,
    strip_fetch_via_meta,
    with_fetch_via,
)


def test_ssrf_error_is_policy():
    assert is_ssrf_or_policy_error("错误：禁止：目标为内网或本机地址") is True
    assert is_ssrf_or_policy_error("错误：HTTP 403") is False


def test_needs_escalation_short_and_block():
    assert (
        needs_escalation("ok " * 5, min_text_chars=200, block_patterns=["just a moment"])
        is True
    )
    long_ok = "正文内容" * 100
    assert (
        needs_escalation(long_ok, min_text_chars=200, block_patterns=["just a moment"])
        is False
    )
    assert (
        needs_escalation(
            "Just a Moment... please wait",
            min_text_chars=200,
            block_patterns=["just a moment"],
        )
        is True
    )
    assert needs_escalation("错误：HTTP 503", min_text_chars=200, block_patterns=[]) is True


def test_fetch_via_meta_roundtrip():
    body = with_fetch_via("hello world", "scrapling")
    assert body.startswith("# fetch_via: scrapling\n")
    assert strip_fetch_via_meta(body) == "hello world"


def test_ssrf_does_not_call_backends():
    calls: list[int] = []

    def boom(_url: str) -> str:
        calls.append(1)
        return "不应调用"

    out = fetch_url_with_escalation(
        "http://127.0.0.1/",
        l1=boom,
        l2=boom,
        l3=boom,
        cfg={"allowed_ports": [80, 443], "escalation": {"enabled": True}},
    )
    assert out.startswith("错误：")
    assert calls == []


def test_escalate_l1_short_to_l2(monkeypatch):
    monkeypatch.setattr(
        "app.advisor.agent.web_fetch_escalation.is_url_safe_for_fetch",
        lambda url, allowed_ports: (True, ""),
    )
    levels: list[str] = []
    out = fetch_url_with_escalation(
        "https://example.com/ok",
        l1=lambda u: "short",
        l2=lambda u: "B" * 250,
        l3=lambda u: "should-not-run",
        on_level=levels.append,
        cfg={
            "allowed_ports": [80, 443],
            "escalation": {
                "enabled": True,
                "min_text_chars": 200,
                "max_total_seconds": 90,
                "enable_stealth": True,
                "block_patterns": [],
            },
        },
    )
    assert "# fetch_via: scrapling" in out
    assert "B" * 50 in out
    assert levels == ["httpx", "scrapling"]


def test_escalate_to_stealth_when_l2_blocked(monkeypatch):
    monkeypatch.setattr(
        "app.advisor.agent.web_fetch_escalation.is_url_safe_for_fetch",
        lambda url, allowed_ports: (True, ""),
    )
    out = fetch_url_with_escalation(
        "https://example.com/cf",
        l1=lambda u: "错误：HTTP 403",
        l2=lambda u: "Just a Moment cloudflare",
        l3=lambda u: "REAL ARTICLE " + ("x" * 200),
        cfg={
            "allowed_ports": [80, 443],
            "escalation": {
                "enabled": True,
                "min_text_chars": 200,
                "enable_stealth": True,
                "block_patterns": ["just a moment"],
            },
        },
    )
    assert "# fetch_via: stealth" in out


def test_escalation_disabled_keeps_short_l1(monkeypatch):
    monkeypatch.setattr(
        "app.advisor.agent.web_fetch_escalation.is_url_safe_for_fetch",
        lambda url, allowed_ports: (True, ""),
    )
    out = fetch_url_with_escalation(
        "https://example.com/x",
        l1=lambda u: "tiny",
        l2=lambda u: "L2" * 200,
        l3=lambda u: "L3" * 200,
        cfg={
            "allowed_ports": [80, 443],
            "escalation": {"enabled": False, "min_text_chars": 200},
        },
    )
    assert "# fetch_via: httpx" in out
    assert "tiny" in out
