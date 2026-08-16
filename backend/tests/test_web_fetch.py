from app.advisor.agent.web_fetch import fetch_url_html, fetch_url_text, is_url_safe_for_fetch


def test_reject_localhost():
    ok, reason = is_url_safe_for_fetch("http://127.0.0.1/", allowed_ports=[80, 443])
    assert ok is False
    assert "内网" in reason or "本机" in reason or "禁止" in reason


def test_reject_private_ip():
    ok, _ = is_url_safe_for_fetch("http://192.168.1.1/", allowed_ports=[80, 443])
    assert ok is False


def test_reject_file_scheme():
    ok, _ = is_url_safe_for_fetch("file:///etc/passwd", allowed_ports=[80, 443])
    assert ok is False


def test_fetch_url_text_rejects_unsafe_without_network():
    out = fetch_url_text("http://127.0.0.1/")
    assert out.startswith("错误：")


def test_fetch_url_html_rejects_unsafe_without_network():
    out = fetch_url_html("http://127.0.0.1/")
    assert out.startswith("错误：")
