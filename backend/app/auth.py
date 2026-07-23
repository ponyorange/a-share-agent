"""Auth: register / login / JWT."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from passlib.context import CryptContext
from pydantic import BaseModel, Field

from .db import ensure_indexes, get_db

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
security = HTTPBearer(auto_error=False)

JWT_ALG = "HS256"
JWT_EXPIRE_DAYS = 14

router = APIRouter(prefix="/api/auth", tags=["auth"])


class RegisterBody(BaseModel):
    username: str = Field(..., min_length=2, max_length=32)
    password: str = Field(..., min_length=4, max_length=64)
    password2: str = Field(..., min_length=4, max_length=64)


class LoginBody(BaseModel):
    username: str
    password: str


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
    return {
        "id": str(user["_id"]),
        "username": user["username"],
    }


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
    return {"token": token, "user": {"id": user_id, "username": username}}


@router.post("/login")
def login(body: LoginBody) -> dict[str, Any]:
    username = body.username.strip()
    db = get_db()
    user = db.users.find_one({"username": username})
    if not user or not verify_password(body.password, user["password_hash"]):
        raise HTTPException(status_code=400, detail="用户名或密码错误")
    user_id = str(user["_id"])
    token = create_token(user_id, username)
    return {"token": token, "user": {"id": user_id, "username": username}}


@router.get("/me")
def me(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    return {"user": user}


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
