import pytest

from app.mail import load_mail_config, send_email


def test_load_mail_config_missing_returns_none(monkeypatch):
    for key in ("MAIL_HOST", "MAIL_PORT", "MAIL_USER", "MAIL_PASS", "MAIL_FROM"):
        monkeypatch.delenv(key, raising=False)
    assert load_mail_config() is None


def test_send_email_without_config_raises(monkeypatch):
    for key in ("MAIL_HOST", "MAIL_PORT", "MAIL_USER", "MAIL_PASS", "MAIL_FROM"):
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(RuntimeError, match="mail_not_configured"):
        send_email("a@example.com", "t", "b")


def test_send_email_uses_smtp_ssl(monkeypatch):
    monkeypatch.setenv("MAIL_HOST", "smtp.163.com")
    monkeypatch.setenv("MAIL_PORT", "465")
    monkeypatch.setenv("MAIL_USER", "u@163.com")
    monkeypatch.setenv("MAIL_PASS", "secret")
    monkeypatch.setenv("MAIL_FROM", "u@163.com")
    calls: dict[str, object] = {}

    class FakeSMTP:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def login(self, user, password):
            calls["login"] = (user, password)

        def send_message(self, msg):
            calls["to"] = msg["To"]
            calls["subject"] = msg["Subject"]

    monkeypatch.setattr("app.mail.smtplib.SMTP_SSL", FakeSMTP)
    send_email("dest@example.com", "主题", "正文")
    assert calls["login"] == ("u@163.com", "secret")
    assert calls["to"] == "dest@example.com"
    assert calls["subject"] == "主题"
