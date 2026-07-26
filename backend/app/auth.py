"""Auth: register / login / JWT / account email & password."""

from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from passlib.context import CryptContext
from pydantic import BaseModel, Field

from .db import ensure_indexes, get_db
from .email_codes import (
    PURPOSE_BIND_EMAIL,
    PURPOSE_RESET_PASSWORD,
    create_and_store_code,
    verify_code,
)
from .mail import send_email

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
security = HTTPBearer(auto_error=False)

JWT_ALG = "HS256"
JWT_EXPIRE_DAYS = 14
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

router = APIRouter(prefix="/api/auth", tags=["auth"])


class RegisterBody(BaseModel):
    username: str = Field(..., min_length=2, max_length=32)
    password: str = Field(..., min_length=4, max_length=64)
    password2: str = Field(..., min_length=4, max_length=64)


class LoginBody(BaseModel):
    username: str
    password: str


class EmailSendCodeBody(BaseModel):
    email: str = Field(..., min_length=3, max_length=254)


class EmailVerifyBody(BaseModel):
    email: str = Field(..., min_length=3, max_length=254)
    code: str = Field(..., min_length=4, max_length=12)


class ChangePasswordBody(BaseModel):
    old_password: str = Field(..., min_length=4, max_length=64)
    new_password: str = Field(..., min_length=4, max_length=64)


class PasswordResetSendBody(BaseModel):
    account: str = Field(..., min_length=1, max_length=254)


class PasswordResetConfirmBody(BaseModel):
    account: str = Field(..., min_length=1, max_length=254)
    code: str = Field(..., min_length=4, max_length=12)
    new_password: str = Field(..., min_length=4, max_length=64)


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return pwd_context.verify(password, password_hash)


def _required_secret(name: str) -> str:
    value = (os.getenv(name) or "").strip()
    if len(value.encode("utf-8")) < 32:
        raise RuntimeError(f"{name} must contain at least 32 bytes")
    return value


def create_token(user_id: str, username: str) -> str:
    payload = {
        "sub": user_id,
        "username": username,
        "exp": datetime.now(timezone.utc) + timedelta(days=JWT_EXPIRE_DAYS),
    }
    return jwt.encode(payload, _required_secret("JWT_SECRET"), algorithm=JWT_ALG)


def decode_token(token: str) -> dict[str, Any]:
    try:
        return jwt.decode(
            token,
            _required_secret("JWT_SECRET"),
            algorithms=[JWT_ALG],
        )
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已失效，请重新登录"
        ) from exc


def _normalize_email(value: str) -> str:
    return value.strip().lower()


def _validate_email(value: str) -> str:
    email = _normalize_email(value)
    if not _EMAIL_RE.fullmatch(email) or len(email) > 254:
        raise HTTPException(status_code=400, detail="email_invalid")
    return email


def _public_user(user: dict[str, Any]) -> dict[str, Any]:
    email = user.get("email")
    verified_at = user.get("email_verified_at")
    return {
        "id": str(user["_id"]),
        "username": user["username"],
        "email": email if isinstance(email, str) else None,
        "email_verified": bool(verified_at),
    }


def _map_runtime_http(exc: RuntimeError) -> HTTPException:
    code = str(exc)
    messages = {
        "mail_not_configured": "邮件服务未配置",
        "mail_send_failed": "邮件发送失败，请稍后重试",
        "code_rate_limited": "验证码发送过于频繁，请稍后再试",
        "code_invalid": "验证码错误",
        "code_expired": "验证码已过期",
        "email_taken": "该邮箱已被占用",
    }
    if code == "email_taken":
        return HTTPException(status_code=400, detail=code)
    if code in messages:
        return HTTPException(status_code=400, detail=code)
    return HTTPException(status_code=400, detail="请求失败")


def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(security),
) -> dict[str, Any]:
    if creds is None or not creds.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="请先登录"
        )
    data = decode_token(creds.credentials)
    user_id = data.get("sub")
    db = get_db()
    user = db.users.find_one({"_id": _oid(user_id)})
    if not user:
        raise HTTPException(status_code=401, detail="用户不存在")
    return _public_user(user)


def get_optional_user(
    creds: HTTPAuthorizationCredentials | None = Depends(security),
) -> dict[str, Any] | None:
    if creds is None or not creds.credentials:
        return None
    try:
        return get_current_user(creds)
    except HTTPException:
        return None


def _oid(value: str):
    from bson import ObjectId

    try:
        return ObjectId(value)
    except Exception as exc:
        raise HTTPException(status_code=401, detail="无效用户") from exc


@router.post("/register")
def register(body: RegisterBody) -> dict[str, Any]:
    username = body.username.strip()
    if not username:
        raise HTTPException(status_code=400, detail="用户名不能为空")
    if body.password != body.password2:
        raise HTTPException(status_code=400, detail="两次密码不一致")
    db = get_db()
    if db.users.find_one({"username": username}):
        raise HTTPException(status_code=400, detail="用户名已存在")
    now = datetime.now(timezone.utc)
    doc = {
        "username": username,
        "password_hash": hash_password(body.password),
        "created_at": now,
    }
    res = db.users.insert_one(doc)
    user_id = str(res.inserted_id)
    # empty portfolio + paper account
    db.portfolios.update_one(
        {"user_id": user_id},
        {"$set": {"user_id": user_id, "positions": [], "updated_at": now}},
        upsert=True,
    )
    db.paper_accounts.update_one(
        {"user_id": user_id},
        {
            "$set": {
                "user_id": user_id,
                "cash": 100_000.0,
                "initial_cash": 100_000.0,
                "updated_at": now,
            }
        },
        upsert=True,
    )
    # 默认策略 + 克隆系统历史推荐
    try:
        from .advisor.migrate_user_data import clone_system_snapshots_to_user
        from .advisor.user_strategy import ensure_user_strategy

        ensure_user_strategy(user_id)
        clone_system_snapshots_to_user(user_id)
    except Exception:
        pass
    token = create_token(user_id, username)
    return {
        "token": token,
        "user": {
            "id": user_id,
            "username": username,
            "email": None,
            "email_verified": False,
        },
    }


@router.post("/login")
def login(body: LoginBody) -> dict[str, Any]:
    username = body.username.strip()
    db = get_db()
    user = db.users.find_one({"username": username})
    if not user or not verify_password(body.password, user["password_hash"]):
        raise HTTPException(status_code=400, detail="用户名或密码错误")
    user_id = str(user["_id"])
    token = create_token(user_id, username)
    return {"token": token, "user": _public_user(user)}


@router.get("/me")
def me(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    return {"user": user}


@router.post("/account/email/send-code")
def account_email_send_code(
    body: EmailSendCodeBody,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    email = _validate_email(body.email)
    db = get_db()
    taken = db.users.find_one(
        {
            "email": email,
            "email_verified_at": {"$ne": None},
            "_id": {"$ne": _oid(user["id"])},
        }
    )
    if taken:
        raise HTTPException(status_code=400, detail="email_taken")
    try:
        code = create_and_store_code(user["id"], email, PURPOSE_BIND_EMAIL)
        send_email(
            email,
            "邮箱绑定验证码",
            f"您的验证码是 {code}，10 分钟内有效。如非本人操作请忽略。",
        )
    except RuntimeError as exc:
        raise _map_runtime_http(exc) from exc
    return {"ok": True, "message": "验证码已发送"}


@router.post("/account/email/verify")
def account_email_verify(
    body: EmailVerifyBody,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    email = _validate_email(body.email)
    db = get_db()
    taken = db.users.find_one(
        {
            "email": email,
            "email_verified_at": {"$ne": None},
            "_id": {"$ne": _oid(user["id"])},
        }
    )
    if taken:
        raise HTTPException(status_code=400, detail="email_taken")
    try:
        verify_code(user["id"], email, PURPOSE_BIND_EMAIL, body.code)
    except RuntimeError as exc:
        raise _map_runtime_http(exc) from exc
    now = datetime.now(timezone.utc)
    db.users.update_one(
        {"_id": _oid(user["id"])},
        {"$set": {"email": email, "email_verified_at": now}},
    )
    refreshed = db.users.find_one({"_id": _oid(user["id"])})
    assert refreshed is not None
    return {"ok": True, "user": _public_user(refreshed)}


@router.post("/account/password")
def account_change_password(
    body: ChangePasswordBody,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    db = get_db()
    doc = db.users.find_one({"_id": _oid(user["id"])})
    if not doc or not verify_password(body.old_password, doc["password_hash"]):
        raise HTTPException(status_code=400, detail="password_incorrect")
    db.users.update_one(
        {"_id": _oid(user["id"])},
        {"$set": {"password_hash": hash_password(body.new_password)}},
    )
    return {"ok": True, "message": "密码已更新"}


_RESET_OK_MESSAGE = "若该账号已绑定邮箱，将收到验证码"


def _find_user_by_account(account: str) -> dict[str, Any] | None:
    db = get_db()
    raw = account.strip()
    if not raw:
        return None
    by_username = db.users.find_one({"username": raw})
    if by_username:
        return by_username
    email = _normalize_email(raw)
    if _EMAIL_RE.fullmatch(email):
        return db.users.find_one({"email": email})
    return None


@router.post("/password-reset/send-code")
def password_reset_send_code(body: PasswordResetSendBody) -> dict[str, Any]:
    user = _find_user_by_account(body.account)
    if (
        user
        and isinstance(user.get("email"), str)
        and user.get("email_verified_at")
    ):
        email = _normalize_email(user["email"])
        try:
            code = create_and_store_code(
                str(user["_id"]),
                email,
                PURPOSE_RESET_PASSWORD,
            )
            send_email(
                email,
                "密码重置验证码",
                f"您的验证码是 {code}，10 分钟内有效。如非本人操作请忽略。",
            )
        except RuntimeError as exc:
            # 对调用方仍返回统一文案，避免枚举；配置缺失时除外给出可操作提示
            if str(exc) == "mail_not_configured":
                raise _map_runtime_http(exc) from exc
            if str(exc) == "code_rate_limited":
                raise _map_runtime_http(exc) from exc
            # mail_send_failed 等：仍不暴露账号是否存在
    return {"ok": True, "message": _RESET_OK_MESSAGE}


@router.post("/password-reset/confirm")
def password_reset_confirm(body: PasswordResetConfirmBody) -> dict[str, Any]:
    user = _find_user_by_account(body.account)
    if (
        not user
        or not isinstance(user.get("email"), str)
        or not user.get("email_verified_at")
    ):
        raise HTTPException(status_code=400, detail="code_invalid")
    email = _normalize_email(user["email"])
    try:
        verify_code(str(user["_id"]), email, PURPOSE_RESET_PASSWORD, body.code)
    except RuntimeError as exc:
        raise _map_runtime_http(exc) from exc
    db = get_db()
    db.users.update_one(
        {"_id": user["_id"]},
        {"$set": {"password_hash": hash_password(body.new_password)}},
    )
    return {"ok": True, "message": "密码已重置"}


def seed_dev_user(positions: list[dict[str, Any]] | None = None) -> bool:
    """Create one opt-in development user without modifying existing users."""
    enabled = (os.getenv("DEV_SEED_ENABLED") or "").strip().lower()
    environment = (os.getenv("APP_ENV") or "production").strip().lower()
    if enabled not in {"1", "true", "yes", "on"} or environment == "production":
        return False
    username = (os.getenv("DEV_SEED_USERNAME") or "").strip()
    password = os.getenv("DEV_SEED_PASSWORD") or ""
    if not username:
        raise RuntimeError("DEV_SEED_USERNAME is required when seeding")
    if (
        len(password) < 16
        or password.lower() == password
        or password.upper() == password
        or not any(char.isdigit() for char in password)
    ):
        raise RuntimeError("DEV_SEED_PASSWORD does not meet strength requirements")
    ensure_indexes()
    db = get_db()
    now = datetime.now(timezone.utc)
    user = db.users.find_one({"username": username})
    if user:
        return False
    res = db.users.insert_one(
        {
            "username": username,
            "password_hash": hash_password(password),
            "created_at": now,
        }
    )
    user_id = str(res.inserted_id)

    if positions is not None:
        db.portfolios.update_one(
            {"user_id": user_id},
            {
                "$set": {
                    "user_id": user_id,
                    "positions": positions,
                    "updated_at": now,
                }
            },
            upsert=True,
        )
    else:
        db.portfolios.update_one(
            {"user_id": user_id},
            {"$setOnInsert": {"user_id": user_id, "positions": [], "updated_at": now}},
            upsert=True,
        )

    db.paper_accounts.update_one(
        {"user_id": user_id},
        {
            "$setOnInsert": {
                "user_id": user_id,
                "cash": 100_000.0,
                "initial_cash": 100_000.0,
                "updated_at": now,
            }
        },
        upsert=True,
    )
    try:
        from .advisor.migrate_user_data import clone_system_snapshots_to_user
        from .advisor.user_strategy import ensure_user_strategy

        ensure_user_strategy(user_id)
        clone_system_snapshots_to_user(user_id)
    except Exception:
        pass
    return True
