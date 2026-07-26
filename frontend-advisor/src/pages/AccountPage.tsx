import { useEffect, useState, type FormEvent } from 'react'
import {
  changePassword,
  fetchMe,
  getUser,
  sendEmailBindCode,
  setSession,
  getToken,
  verifyEmailBind,
  type AuthUser,
} from '../auth'

export default function AccountPage() {
  const [user, setUser] = useState<AuthUser | null>(() => getUser())
  const [email, setEmail] = useState('')
  const [code, setCode] = useState('')
  const [oldPassword, setOldPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [newPassword2, setNewPassword2] = useState('')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [msg, setMsg] = useState<string | null>(null)

  useEffect(() => {
    fetchMe()
      .then((res) => {
        setUser(res.user)
        const token = getToken()
        if (token) setSession(token, res.user)
        if (res.user.email) setEmail(res.user.email)
      })
      .catch((err) => setError(err instanceof Error ? err.message : String(err)))
      .finally(() => setLoading(false))
  }, [])

  async function onSendCode() {
    setSaving(true)
    setError(null)
    setMsg(null)
    try {
      const res = await sendEmailBindCode(email.trim())
      setMsg(res.message || '验证码已发送')
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setSaving(false)
    }
  }

  async function onVerifyEmail(e: FormEvent) {
    e.preventDefault()
    setSaving(true)
    setError(null)
    setMsg(null)
    try {
      const res = await verifyEmailBind(email.trim(), code.trim())
      setUser(res.user)
      const token = getToken()
      if (token) setSession(token, res.user)
      setMsg('邮箱已绑定')
      setCode('')
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setSaving(false)
    }
  }

  async function onChangePassword(e: FormEvent) {
    e.preventDefault()
    if (newPassword !== newPassword2) {
      setError('两次新密码不一致')
      return
    }
    setSaving(true)
    setError(null)
    setMsg(null)
    try {
      const res = await changePassword(oldPassword, newPassword)
      setMsg(res.message || '密码已更新')
      setOldPassword('')
      setNewPassword('')
      setNewPassword2('')
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setSaving(false)
    }
  }

  return (
    <section className="page">
      <div className="page-hero">
        <p>管理账号邮箱与登录密码。已验证邮箱可用于找回密码，以及让投研助手发送聊天摘要。</p>
      </div>

      {loading ? <p className="status">加载中…</p> : null}
      {error ? <p className="status error">{error}</p> : null}
      {msg ? <p className="status ok">{msg}</p> : null}

      <section className="card-block">
        <h2 className="section-title">账号</h2>
        <p className="muted">用户名：{user?.username || '—'}</p>
      </section>

      <section className="card-block">
        <h2 className="section-title">邮箱</h2>
        <p className="muted">
          当前：
          {user?.email_verified && user.email
            ? `${user.email}（已验证）`
            : user?.email
              ? `${user.email}（未验证）`
              : '未绑定'}
        </p>
        <form className="auth-form" onSubmit={onVerifyEmail}>
          <label>
            邮箱
            <input
              className="input"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              autoComplete="email"
            />
          </label>
          <div className="btn-row">
            <button
              type="button"
              className="btn ghost"
              disabled={saving || !email.trim()}
              onClick={() => void onSendCode()}
            >
              发送验证码
            </button>
          </div>
          <label>
            验证码
            <input
              className="input"
              value={code}
              onChange={(e) => setCode(e.target.value)}
              autoComplete="one-time-code"
            />
          </label>
          <button className="btn" type="submit" disabled={saving || !code.trim()}>
            验证并保存
          </button>
        </form>
      </section>

      <section className="card-block">
        <h2 className="section-title">修改密码</h2>
        <form className="auth-form" onSubmit={onChangePassword}>
          <label>
            旧密码
            <input
              className="input"
              type="password"
              value={oldPassword}
              onChange={(e) => setOldPassword(e.target.value)}
              autoComplete="current-password"
            />
          </label>
          <label>
            新密码
            <input
              className="input"
              type="password"
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
              autoComplete="new-password"
            />
          </label>
          <label>
            确认新密码
            <input
              className="input"
              type="password"
              value={newPassword2}
              onChange={(e) => setNewPassword2(e.target.value)}
              autoComplete="new-password"
            />
          </label>
          <button className="btn" type="submit" disabled={saving}>
            更新密码
          </button>
        </form>
      </section>
    </section>
  )
}
