# User Email, Password Reset & Agent Mail Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 支持资料页绑定已验证邮箱、登录后旧密码改密、未登录忘记密码，以及 Agent 在确认后把聊天摘要发到用户邮箱。

**Architecture:** 扩展 Mongo `users` 与 `email_verification_codes`；新增 `app/mail.py`（163 SMTP SSL）；auth 路由承担绑定/改密/重置；前端 `/account` + 登录页忘记密码；主 Agent 工具 `send_chat_summary_email` 走 `confirm` 预览。

**Tech Stack:** FastAPI、Pydantic 2、pymongo、passlib/bcrypt、smtplib、pytest、React 19、React Router 7、Vitest。

## Global Constraints

- 真实 `MAIL_PASS` 只写本机/部署 `.env`，禁止提交 git / 镜像 / 文档。
- `.env.example` 仅占位符与注释。
- 注册不强制邮箱；Agent/重置密码仅使用已验证邮箱。
- 登录改密只用旧密码；忘记密码走验证码。
- Agent 发信必须 `confirm=false` 预览后再 `confirm=true`。
- 前端页面无页面级 `h1`（page title 策略）。
- 验证码哈希存储；10 分钟过期；同用途发码冷却 60s；防枚举文案。

## File Map

| File | Responsibility |
|------|----------------|
| `backend/app/mail.py` | SMTP 配置读取与发送 |
| `backend/app/email_codes.py` | 验证码创建/校验/冷却 |
| `backend/app/auth.py` | `/me` 扩展；绑定邮箱；改密；忘记密码 API |
| `backend/app/db.py` | `users.email` sparse unique；codes 索引 |
| `backend/.env.example` / `deploy/.env.example` | `MAIL_*` 占位 |
| `frontend-advisor/src/auth.ts` | `AuthUser` + account/reset API helpers |
| `frontend-advisor/src/pages/AccountPage.tsx` | 资料页 |
| `frontend-advisor/src/pages/LoginPage.tsx` | 忘记密码 UI |
| `frontend-advisor/src/App.tsx` | 用户名链接 + `/account` 路由 |
| `backend/app/advisor/agent/tools.py` | `send_chat_summary_email` |
| `backend/app/advisor/agent/graph.py` | prompt 发信规则 |
| `backend/tests/test_mail.py` 等 | 后端测试 |
| `frontend-advisor/src/pages/AccountPage.test.tsx` 等 | 前端测试 |

---

### Task 1: SMTP 邮件模块

**Files:**
- Create: `backend/app/mail.py`
- Create: `backend/tests/test_mail.py`
- Modify: `backend/.env.example`
- Modify: `deploy/.env.example`

**Interfaces:**
- Produces: `MailConfig`, `load_mail_config() -> MailConfig | None`, `send_email(to: str, subject: str, body_text: str) -> None`（失败抛 `RuntimeError` 带稳定 code：`mail_not_configured` / `mail_send_failed`）

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_mail.py
import os
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
```

- [ ] **Step 2: 跑测试确认失败**

```bash
PYTHONPATH=backend backend/.venv/bin/pytest -q backend/tests/test_mail.py
```

Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现 `mail.py`**

```python
# backend/app/mail.py（要点）
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
    return MailConfig(host=host, port=port, user=user, password=password, mail_from=mail_from)

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
```

在两个 `.env.example` 追加：

```bash
# 163 SMTP（授权码，非登录密码；真实值只放部署机 .env）
MAIL_HOST=smtp.163.com
MAIL_PORT=465
MAIL_USER=
MAIL_PASS=
MAIL_FROM=
```

- [ ] **Step 4: 补 mock 发送成功测试并跑通**

```python
def test_send_email_uses_smtp_ssl(monkeypatch):
    monkeypatch.setenv("MAIL_HOST", "smtp.163.com")
    monkeypatch.setenv("MAIL_PORT", "465")
    monkeypatch.setenv("MAIL_USER", "u@163.com")
    monkeypatch.setenv("MAIL_PASS", "secret")
    monkeypatch.setenv("MAIL_FROM", "u@163.com")
    calls = {}
    class FakeSMTP:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def login(self, user, password):
            calls["login"] = (user, password)
        def send_message(self, msg):
            calls["to"] = msg["To"]
            calls["subject"] = msg["Subject"]
    monkeypatch.setattr("app.mail.smtplib.SMTP_SSL", FakeSMTP)
    send_email("dest@example.com", "主题", "正文")
    assert calls["login"] == ("u@163.com", "secret")
    assert calls["to"] == "dest@example.com"
```

```bash
PYTHONPATH=backend backend/.venv/bin/pytest -q backend/tests/test_mail.py
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/mail.py backend/tests/test_mail.py backend/.env.example deploy/.env.example
git commit -m "feat: add SMTP mail helper for 163"
```

---

### Task 2: 验证码存储与校验

**Files:**
- Create: `backend/app/email_codes.py`
- Create: `backend/tests/test_email_codes.py`
- Modify: `backend/app/db.py`（`ensure_indexes`）

**Interfaces:**
- Consumes: `get_db()`, `hash_password`/`verify_password` 或独立 sha256 哈希
- Produces:
  - `PURPOSE_BIND_EMAIL = "bind_email"`
  - `PURPOSE_RESET_PASSWORD = "reset_password"`
  - `create_and_store_code(user_id: str, email: str, purpose: str) -> str`（返回明文码仅用于发信；冷却则抛 `code_rate_limited`）
  - `verify_code(user_id: str, email: str, purpose: str, code: str) -> None`（失败抛 `code_invalid` / `code_expired`）

- [ ] **Step 1: 写失败测试（用 mongomock 或现有测试 DB 夹具；跟随仓库既有 auth/db 测试风格）**

```python
# backend/tests/test_email_codes.py
import pytest
from app.email_codes import (
    PURPOSE_BIND_EMAIL,
    create_and_store_code,
    verify_code,
)

# 使用项目已有的 mongodb 测试夹具；若无，用 monkeypatch 替换 get_db 为内存假实现。

def test_code_roundtrip(db_fixture):
    code = create_and_store_code("u1", "a@example.com", PURPOSE_BIND_EMAIL)
    assert len(code) == 6 and code.isdigit()
    verify_code("u1", "a@example.com", PURPOSE_BIND_EMAIL, code)

def test_wrong_code_raises(db_fixture):
    create_and_store_code("u1", "a@example.com", PURPOSE_BIND_EMAIL)
    with pytest.raises(RuntimeError, match="code_invalid"):
        verify_code("u1", "a@example.com", PURPOSE_BIND_EMAIL, "000000")

def test_rate_limit(db_fixture):
    create_and_store_code("u1", "a@example.com", PURPOSE_BIND_EMAIL)
    with pytest.raises(RuntimeError, match="code_rate_limited"):
        create_and_store_code("u1", "a@example.com", PURPOSE_BIND_EMAIL)
```

- [ ] **Step 2: 实现 `email_codes.py` + 索引**

要点：
- 6 位 `secrets.randbelow`
- `code_hash = hashlib.sha256(f"{user_id}:{purpose}:{code}".encode()).hexdigest()`（或 hmac）
- `expires_at = now + 10min`；`attempts`；上限 5
- 冷却：同 `user_id+purpose` 且 `created_at` 在 60s 内 → `code_rate_limited`
- 校验成功后删除该用途未用码
- `db.py`：

```python
db.users.create_index(
    "email",
    unique=True,
    partialFilterExpression={"email": {"$type": "string"}},
)
db.email_verification_codes.create_index(
    [("user_id", 1), ("purpose", 1), ("created_at", -1)]
)
db.email_verification_codes.create_index("expires_at", expireAfterSeconds=0)  # 若用 TTL 需 expires_at 为日期；否则跳过 TTL，靠逻辑过期
```

若 TTL 与「逻辑过期」冲突，本任务只用逻辑 `expires_at` 判断，可不建 TTL。

- [ ] **Step 3: 跑测试**

```bash
PYTHONPATH=backend backend/.venv/bin/pytest -q backend/tests/test_email_codes.py
```

Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add backend/app/email_codes.py backend/app/db.py backend/tests/test_email_codes.py
git commit -m "feat: add email verification code store"
```

---

### Task 3: 资料页 API（绑定邮箱 + 登录改密）与 `/me` 扩展

**Files:**
- Modify: `backend/app/auth.py`
- Create: `backend/tests/test_auth_account.py`

**Interfaces:**
- Consumes: `send_email`, `create_and_store_code`, `verify_code`
- Produces:
  - `get_current_user` / `me` 返回 `email: str | None`, `email_verified: bool`
  - `POST /api/auth/account/email/send-code` `{email}`
  - `POST /api/auth/account/email/verify` `{email, code}`
  - `POST /api/auth/account/password` `{old_password, new_password}`

- [ ] **Step 1: 写 API 测试（TestClient + 测试用户）**

覆盖：
1. `/me` 初始 `email` null、`email_verified` false
2. send-code → mock `send_email` → verify → `/me` 有邮箱且 verified
3. 邮箱被占用 → 400/`email_taken`
4. 改密：旧密码错误失败；正确则可用新密码登录

- [ ] **Step 2: 实现 auth 扩展**

要点：
- `_public_user(doc) -> dict` 统一构造公开字段
- `get_current_user` 从 DB 读 `email` / `email_verified_at`
- 邮箱 `email.strip().lower()`；简单正则或 `EmailStr`
- bind：若其他用户已占用该 email 且已验证 → `email_taken`
- verify 成功：`$set email, email_verified_at`
- password：`verify_password(old)` → `hash_password(new)`；`new` 长度 4..64

- [ ] **Step 3: 跑测试**

```bash
PYTHONPATH=backend backend/.venv/bin/pytest -q backend/tests/test_auth_account.py
```

Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add backend/app/auth.py backend/tests/test_auth_account.py
git commit -m "feat: bind verified email and change password APIs"
```

---

### Task 4: 忘记密码 API

**Files:**
- Modify: `backend/app/auth.py`
- Modify: `backend/tests/test_auth_account.py`（或 `test_auth_password_reset.py`）

**Interfaces:**
- Produces:
  - `POST /api/auth/password-reset/send-code` `{account}` → 始终类似 `{"ok": true, "message": "若该账号已绑定邮箱，将收到验证码"}`
  - `POST /api/auth/password-reset/confirm` `{account, code, new_password}`

- [ ] **Step 1: 写测试**

1. 有已验证邮箱：send-code 调用 `send_email` 一次；confirm 后可用新密码登录
2. 无邮箱/不存在用户：send-code 仍 200，且不调用 `send_email`
3. 错误验证码：confirm 失败

- [ ] **Step 2: 实现**

```python
# 伪代码
user = db.users.find_one({"$or": [{"username": account}, {"email": account.lower()}]})
if user and user.get("email") and user.get("email_verified_at"):
    code = create_and_store_code(...)
    send_email(user["email"], "密码重置验证码", f"您的验证码是 {code}，10 分钟内有效。")
return {"ok": True, "message": "若该账号已绑定邮箱，将收到验证码"}
```

- [ ] **Step 3: 跑测试并 Commit**

```bash
PYTHONPATH=backend backend/.venv/bin/pytest -q backend/tests/test_auth_account.py
git add backend/app/auth.py backend/tests/test_auth_account.py
git commit -m "feat: add forgot-password email reset API"
```

---

### Task 5: 前端资料页与用户名入口

**Files:**
- Modify: `frontend-advisor/src/auth.ts`
- Create: `frontend-advisor/src/pages/AccountPage.tsx`
- Create: `frontend-advisor/src/pages/AccountPage.test.tsx`
- Modify: `frontend-advisor/src/App.tsx`
- Modify: `frontend-advisor/src/App.test.tsx`（如有路由断言）
- Modify: `frontend-advisor/src/styles.css`（用户名可点击样式，最小改动）

**Interfaces:**
- Produces: `AuthUser = { id, username, email?: string | null, email_verified?: boolean }`
- API helpers: `sendEmailBindCode`, `verifyEmailBind`, `changePassword`

- [ ] **Step 1: 扩展 `auth.ts` 与 AccountPage 测试（用户名 link、表单字段）**

- [ ] **Step 2: 实现 `AccountPage`**

结构对齐 `AgentSettingsPage`：
- `page-hero` + `h2.section-title`「账号」/「邮箱」/「修改密码」
- 展示当前邮箱与验证状态
- 绑定：email input + 发送验证码 + code input + 保存
- 改密：旧/新/确认

- [ ] **Step 3: `App.tsx`**

```tsx
<Link className="user-name" to="/account">{user.username}</Link>
// routes
<Route path="/account" element={<AccountPage />} />
```

登录/注册成功后 `setSession` 的 user 含 email 字段；`fetchMe` 刷新。

- [ ] **Step 4: 跑前端测试**

```bash
cd frontend-advisor && npm test -- --run src/pages/AccountPage.test.tsx src/App.test.tsx
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend-advisor/src/auth.ts frontend-advisor/src/pages/AccountPage.tsx \
  frontend-advisor/src/pages/AccountPage.test.tsx frontend-advisor/src/App.tsx \
  frontend-advisor/src/App.test.tsx frontend-advisor/src/styles.css
git commit -m "feat: add account page and username entry"
```

---

### Task 6: 登录页忘记密码

**Files:**
- Modify: `frontend-advisor/src/pages/LoginPage.tsx`
- Modify: `frontend-advisor/src/auth.ts`（reset helpers）
- Create/Modify: `frontend-advisor/src/pages/LoginPage.test.tsx`（若已有则扩展）

- [ ] **Step 1: 增加「忘记密码」入口与分步 UI**

步骤：输入账号 → 发送验证码 → 输入验证码+新密码 → 成功后回到登录

- [ ] **Step 2: 测试入口可见与关键 API 调用 mock**

- [ ] **Step 3: Commit**

```bash
git add frontend-advisor/src/pages/LoginPage.tsx frontend-advisor/src/auth.ts \
  frontend-advisor/src/pages/LoginPage.test.tsx
git commit -m "feat: add forgot-password flow on login page"
```

---

### Task 7: Agent 发送聊天摘要邮件

**Files:**
- Modify: `backend/app/advisor/agent/tools.py`
- Modify: `backend/app/advisor/agent/graph.py`
- Create: `backend/tests/test_agent_email_tool.py`
- Modify: `backend/tests/test_data_agent_delegate.py`（若断言工具列表/prompt，同步）

**Interfaces:**
- Produces: `send_chat_summary_email(subject: str, summary_markdown: str, confirm: bool = False) -> str`
- 读用户：`get_db().users.find_one` 取已验证 `email`
- 预览：`_need_confirm("send_chat_summary_email", {to, subject, summary_preview})`
- 发送：`send_email(to, subject, summary_markdown)`

- [ ] **Step 1: 写工具测试**

1. 无邮箱 → error `email_not_verified`
2. `confirm=false` → `needs_confirm` 且不调用 send
3. `confirm=true` → 调用 send_email

- [ ] **Step 2: 实现工具并挂到 `build_tools`；prompt 增加规则**

```text
19. 将聊天摘要发到用户邮箱：使用 send_chat_summary_email；
   先 confirm=false 预览收件人/主题/摘要，用户明确同意后再 confirm=true。
   无已验证邮箱时引导去个人资料页绑定；禁止编造收件人。
```

（若 18 已占用 Python 规则，本条用下一个序号。）

- [ ] **Step 3: 跑测试**

```bash
PYTHONPATH=backend backend/.venv/bin/pytest -q \
  backend/tests/test_agent_email_tool.py \
  backend/tests/test_data_agent_delegate.py::test_main_agent_registers_delegate_last_and_preserves_specialized_rules
```

Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add backend/app/advisor/agent/tools.py backend/app/advisor/agent/graph.py \
  backend/tests/test_agent_email_tool.py backend/tests/test_data_agent_delegate.py
git commit -m "feat: agent tool to email chat summary with confirm"
```

---

### Task 8: 本地配置说明与回归

**Files:**
- Modify: `README.md`（简短 MAIL_* 说明，无真实密钥）
- 可选：本机 `backend/.env` 由开发者自行添加（**不要 git add .env**）

- [ ] **Step 1: README 增加「邮件（可选）」小节**：MAIL_*、授权码获取方式、绑定邮箱与忘记密码入口

- [ ] **Step 2: 回归**

```bash
PYTHONPATH=backend backend/.venv/bin/pytest -q \
  backend/tests/test_mail.py \
  backend/tests/test_email_codes.py \
  backend/tests/test_auth_account.py \
  backend/tests/test_agent_email_tool.py
cd frontend-advisor && npm test -- --run src/pages/AccountPage.test.tsx src/App.test.tsx
```

Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: document optional SMTP mail settings"
```

---

## Spec Coverage Check

| Spec 项 | Task |
|---------|------|
| `/account` + 用户名入口 | 5 |
| 绑定邮箱验证码 | 2, 3, 5 |
| 163 SMTP | 1 |
| 登录改密（旧密码） | 3, 5 |
| 忘记密码 | 4, 6 |
| Agent 摘要邮件 + confirm | 7 |
| 密钥不进仓 / example | 1, 8 |
| 防枚举 / 限流 | 2, 4 |
| 测试 | 各 Task |

## Placeholder Scan

无 TBD/TODO；步骤含具体代码与命令。

## Type Consistency

- `email_verified`（API bool）↔ `email_verified_at`（DB datetime）
- purposes: `bind_email` / `reset_password`
- 工具名：`send_chat_summary_email`
