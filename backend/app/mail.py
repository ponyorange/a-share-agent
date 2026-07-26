"""SMTP email sending (163-compatible SSL)."""

from __future__ import annotations

import os
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage


@dataclass(frozen=True)
class MailConfig:
    host: str
    port: int
    user: str
    password: str
    mail_from: str


def load_mail_config() -> MailConfig | None:
    host = (os.getenv("MAIL_HOST") or "").strip()
    user = (os.getenv("MAIL_USER") or "").strip()
    password = (os.getenv("MAIL_PASS") or "").strip()
    mail_from = (os.getenv("MAIL_FROM") or "").strip() or user
    port_raw = (os.getenv("MAIL_PORT") or "465").strip()
    if not host or not user or not password or not mail_from:
        return None
    try:
        port = int(port_raw)
    except ValueError:
        return None
    if port <= 0:
        return None
    return MailConfig(
        host=host,
        port=port,
        user=user,
        password=password,
        mail_from=mail_from,
    )


def send_email(to: str, subject: str, body_text: str) -> None:
    cfg = load_mail_config()
    if cfg is None:
        raise RuntimeError("mail_not_configured")
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg.mail_from
    msg["To"] = to
    msg.set_content(body_text)
    try:
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(cfg.host, cfg.port, context=context, timeout=20) as smtp:
            smtp.login(cfg.user, cfg.password)
            smtp.send_message(msg)
    except Exception as exc:
        raise RuntimeError("mail_send_failed") from exc
