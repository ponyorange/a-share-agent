import { useState, type FormEvent } from 'react'
import {
  clearSession,
  confirmPasswordReset,
  login,
  register,
  sendPasswordResetCode,
  setSession,
  type AuthUser,
} from '../auth'

type Props = {
  onAuthed: (user: AuthUser) => void
}

type Mode = 'login' | 'register' | 'reset'

export default function LoginPage({ onAuthed }: Props) {
  const [mode, setMode] = useState<Mode>('login')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [password2, setPassword2] = useState('')
  const [account, setAccount] = useState('')
  const [code, setCode] = useState('')
  const [resetPassword, setResetPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [msg, setMsg] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  async function onSubmit(e: FormEvent) {
    e.preventDefault()
    setLoading(true)
    setError(null)
    setMsg(null)
    try {
      const res =
        mode === 'login'
          ? await login(username.trim(), password)
          : await register(username.trim(), password, password2)
      setSession(res.token, res.user)
      onAuthed(res.user)
    } catch (err) {
      clearSession()
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setLoading(false)
    }
  }

  async function onSendResetCode() {
    setLoading(true)
    setError(null)
    setMsg(null)
    try {
      const res = await sendPasswordResetCode(account.trim())
      setMsg(res.message)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setLoading(false)
    }
  }

  async function onConfirmReset(e: FormEvent) {
    e.preventDefault()
    setLoading(true)
    setError(null)
    setMsg(null)
    try {
      const res = await confirmPasswordReset(account.trim(), code.trim(), resetPassword)
      setMsg(res.message || '密码已重置，请登录')
      setMode('login')
      setUsername(account.trim())
      setPassword('')
      setCode('')
      setResetPassword('')
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setLoading(false)
    }
  }

  if (mode === 'reset') {
    return (
      <section className="page auth-page">
        <div className="page-hero">
          <h1>次日顾问</h1>
          <p>通过已验证邮箱收取验证码后重置密码。</p>
        </div>
        <form className="auth-form" onSubmit={onConfirmReset}>
          <label>
            用户名或邮箱
            <input
              className="input"
              value={account}
              onChange={(e) => setAccount(e.target.value)}
              autoComplete="username"
            />
          </label>
          <div className="btn-row">
            <button
              type="button"
              className="btn ghost"
              disabled={loading || !account.trim()}
              onClick={() => void onSendResetCode()}
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
          <label>
            新密码
            <input
              className="input"
              type="password"
              value={resetPassword}
              onChange={(e) => setResetPassword(e.target.value)}
              autoComplete="new-password"
            />
          </label>
          {error ? <p className="status error">{error}</p> : null}
          {msg ? <p className="status ok">{msg}</p> : null}
          <button className="btn" type="submit" disabled={loading}>
            {loading ? '提交中…' : '重置密码'}
          </button>
          <button type="button" className="btn ghost" onClick={() => setMode('login')}>
            返回登录
          </button>
        </form>
      </section>
    )
  }

  return (
    <section className="page auth-page">
      <div className="page-hero">
        <h1>次日顾问</h1>
        <p>登录后可管理持仓、查看推荐归档与模拟盘。</p>
      </div>
      <form className="auth-form" onSubmit={onSubmit}>
        <label>
          用户名
          <input
            className="input"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            autoComplete="username"
          />
        </label>
        <label>
          密码
          <input
            className="input"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
          />
        </label>
        {mode === 'register' ? (
          <label>
            确认密码
            <input
              className="input"
              type="password"
              value={password2}
              onChange={(e) => setPassword2(e.target.value)}
              autoComplete="new-password"
            />
          </label>
        ) : null}
        {error ? <p className="status error">{error}</p> : null}
        {msg ? <p className="status ok">{msg}</p> : null}
        <button className="btn" type="submit" disabled={loading}>
          {loading ? '提交中…' : mode === 'login' ? '登录' : '注册'}
        </button>
        {mode === 'login' ? (
          <button type="button" className="btn ghost" onClick={() => setMode('reset')}>
            忘记密码
          </button>
        ) : null}
        <button
          type="button"
          className="btn ghost"
          onClick={() => setMode(mode === 'login' ? 'register' : 'login')}
        >
          {mode === 'login' ? '没有账号？注册' : '已有账号？登录'}
        </button>
      </form>
    </section>
  )
}
