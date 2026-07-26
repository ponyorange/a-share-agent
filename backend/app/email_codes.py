"""Email verification codes for bind-email and password-reset."""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from .db import get_db

PURPOSE_BIND_EMAIL = "bind_email"
PURPOSE_RESET_PASSWORD = "reset_password"
_CODE_TTL = timedelta(minutes=10)
_COOLDOWN = timedelta(seconds=60)
_MAX_ATTEMPTS = 5


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _hash_code(user_id: str, purpose: str, code: str) -> str:
    material = f"{user_id}:{purpose}:{code}".encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def create_and_store_code(user_id: str, email: str, purpose: str) -> str:
    if purpose not in {PURPOSE_BIND_EMAIL, PURPOSE_RESET_PASSWORD}:
        raise RuntimeError("invalid_purpose")
    db = get_db()
    now = _utcnow()
    recent = db.email_verification_codes.find_one(
        {
            "user_id": user_id,
            "purpose": purpose,
            "created_at": {"$gte": now - _COOLDOWN},
        }
    )
    if recent is not None:
        raise RuntimeError("code_rate_limited")

    code = f"{secrets.randbelow(1_000_000):06d}"
    db.email_verification_codes.insert_one(
        {
            "user_id": user_id,
            "email": email.strip().lower(),
            "purpose": purpose,
            "code_hash": _hash_code(user_id, purpose, code),
            "expires_at": now + _CODE_TTL,
            "attempts": 0,
            "created_at": now,
        }
    )
    return code


def verify_code(user_id: str, email: str, purpose: str, code: str) -> None:
    if purpose not in {PURPOSE_BIND_EMAIL, PURPOSE_RESET_PASSWORD}:
        raise RuntimeError("invalid_purpose")
    db = get_db()
    now = _utcnow()
    email_norm = email.strip().lower()
    doc = db.email_verification_codes.find_one(
        {
            "user_id": user_id,
            "purpose": purpose,
            "email": email_norm,
        },
        sort=[("created_at", -1)],
    )
    if doc is None:
        raise RuntimeError("code_invalid")

    expires_at = doc.get("expires_at")
    if isinstance(expires_at, datetime):
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at < now:
            db.email_verification_codes.delete_one({"_id": doc["_id"]})
            raise RuntimeError("code_expired")

    attempts = int(doc.get("attempts") or 0)
    if attempts >= _MAX_ATTEMPTS:
        db.email_verification_codes.delete_one({"_id": doc["_id"]})
        raise RuntimeError("code_invalid")

    expected = doc.get("code_hash")
    actual = _hash_code(user_id, purpose, (code or "").strip())
    if not expected or not secrets.compare_digest(str(expected), actual):
        db.email_verification_codes.update_one(
            {"_id": doc["_id"]},
            {"$set": {"attempts": attempts + 1}},
        )
        raise RuntimeError("code_invalid")

    db.email_verification_codes.delete_many(
        {"user_id": user_id, "purpose": purpose, "email": email_norm}
    )
