# 用户邮箱、密码重置与 Agent 邮件摘要设计

## 目标

1. 顶栏用户名可进入个人资料页，绑定/修改邮箱（验证码确认），用于提醒与 Agent 发信。
2. 接入 163 SMTP 发信（配置仅来自环境变量，密钥不进仓库）。
3. 登录后可用旧密码修改密码；未登录支持「忘记密码」（用户名或邮箱 → 验证码 → 重置）。
4. 主 Agent 可在用户确认后，将聊天摘要发送到用户已验证邮箱。

## 已确认决策

- 邮箱验证仅用于资料页绑定/改邮箱（注册流程不强制邮箱）。
- 登录后改密：旧密码验证即可，不走邮箱。
- 忘记密码：登录页入口；用户名或邮箱定位账号，验证码发到已验证邮箱。
- Agent 发信：预览后确认（`confirm=false` → 用户同意 → `confirm=true`），与现有写操作一致。
- 实现路径：扩展 `users` 文档 + 共用 `app/mail.py` SMTP 模块。

## 架构

```text
顶栏用户名 → /account
  ├─ 绑定/改邮箱：send-code → verify → users.email + email_verified_at
  └─ 改密码：旧密码 + 新密码

登录页「忘记密码」
  └─ 用户名或邮箱 → send-code → confirm → 重置 password_hash

.env MAIL_* → app/mail.py (SMTP SSL 465)
  ├─ 绑定验证码邮件
  ├─ 重置密码验证码邮件
  └─ Agent 聊天摘要邮件

Agent: send_chat_summary_email(..., confirm)
  └─ 仅发往已验证邮箱
```

## 数据模型

### `users` 扩展

| 字段 | 说明 |
|------|------|
| `email` | 可选；规范化小写；sparse unique |
| `email_verified_at` | 验证通过时间；未验证视为不可用于发信/重置 |

公开 `GET /api/auth/me` 增加：`email`、`email_verified`（bool）。

### `email_verification_codes`

| 字段 | 说明 |
|------|------|
| `purpose` | `bind_email` \| `reset_password` |
| `user_id` | 目标用户 |
| `email` | 收件地址（绑定为新邮箱；重置为已验证邮箱） |
| `code_hash` | 验证码哈希（不明文存） |
| `expires_at` | 默认 10 分钟 |
| `attempts` | 校验失败次数；达上限作废 |
| `created_at` | 用于冷却（同用户同用途约 60s） |

## SMTP 配置

环境变量（`.env` / 部署机，**禁止提交真实值**）：

```bash
MAIL_HOST=smtp.163.com
MAIL_PORT=465
MAIL_USER=
MAIL_PASS=          # 163 授权码，非登录密码
MAIL_FROM=          # 通常与 MAIL_USER 相同
```

`backend/.env.example` 与 `deploy/.env.example` 仅写占位与注释。模块在缺配置时返回 `mail_not_configured`，不崩溃。

## API（`/api/auth`）

| 方法 | 路径 | 鉴权 | 作用 |
|------|------|------|------|
| GET | `/me` | 是 | 含 `email`、`email_verified` |
| POST | `/account/email/send-code` | 是 | body: `{ email }`，向新邮箱发绑定码 |
| POST | `/account/email/verify` | 是 | body: `{ email, code }`，验证后写入 |
| POST | `/account/password` | 是 | body: `{ old_password, new_password }` |
| POST | `/password-reset/send-code` | 否 | body: `{ account }`（用户名或邮箱）；统一成功文案防枚举 |
| POST | `/password-reset/confirm` | 否 | body: `{ account, code, new_password }` |

校验规则与现有密码策略对齐（长度等）；邮箱格式校验；占用冲突返回明确错误。

## 前端

- `App.tsx`：用户名改为可点击，导航 `/account`。
- 新页 `AccountPage`：无页面级 `h1`（遵守 page title 策略）；绑定邮箱、改密。
- `LoginPage`：增加「忘记密码」流程（可同页分步或子视图）。
- `AuthUser` 扩展 `email` / `email_verified`；登录后刷新 `me`。

## Agent 工具

- 名称：`send_chat_summary_email`
- 参数：`subject: str`、`summary_markdown: str`、`confirm: bool = False`
- 行为：
  - 无已验证邮箱 → 错误，引导 `/account`
  - `confirm=false` → 返回预览（收件人、主题、摘要摘要）
  - `confirm=true` → SMTP 发送纯文本/简单 multipart
- System prompt：发信属写操作，须先预览再确认；禁止编造收件人。

摘要内容由模型整理当前会话要点后传入工具；工具不自动抓取完整原始 tool 输出进邮件（避免泄露过大/敏感细节），以模型提供的 `summary_markdown` 为准。

## 安全与限流

- 验证码 6 位数字；哈希存储；10 分钟过期；失败次数上限后作废。
- 同用户同用途发码冷却约 60 秒。
- 重置密码：账号不存在/未验证邮箱时仍返回通用成功提示（「若该账号已绑定邮箱，将收到验证码」）。
- 日志禁止打印 `MAIL_PASS`、验证码明文、完整授权头。
- 对话或文档中出现过的授权码视为已暴露，部署前应轮换。

## 错误码（示例）

| code | 场景 |
|------|------|
| `mail_not_configured` | 缺少 MAIL_* |
| `mail_send_failed` | SMTP 发送失败 |
| `email_invalid` | 邮箱格式非法 |
| `email_taken` | 邮箱已被占用 |
| `code_invalid` / `code_expired` | 验证码错误或过期 |
| `code_rate_limited` | 发码过频 |
| `email_not_verified` | Agent/重置需要已验证邮箱 |
| `password_incorrect` | 旧密码错误 |

## 测试重点

1. 绑定邮箱：发码、校验、占用冲突、冷却。
2. 登录改密：旧密码对/错。
3. 忘记密码：已验证可重置；未绑定不暴露是否存在；验证码错误。
4. `mail.py`：mock SMTP；缺配置。
5. Agent 工具：预览、确认发送、无邮箱拒绝。
6. 前端：用户名进资料页；忘记密码入口。

## 不做（本迭代）

- 注册强制邮箱
- 营销/批量邮件、附件、可视化模板编辑器
- 第三方邮件 SaaS
- 站内通知中心
- 将真实 `MAIL_PASS` 写入 git 或镜像

## 验收

1. 点击用户名进入资料页，可验证绑定邮箱。
2. 登录后可用旧密码改密。
3. 登录页可完成忘记密码（验证码到已验证邮箱）。
4. Agent 可预览并在确认后发送聊天摘要到该邮箱。
5. 未配置 SMTP 时行为可预期且不崩溃。
