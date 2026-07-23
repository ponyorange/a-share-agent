import { useState, type FormEvent } from 'react'
import { clearSession, login, register, setSession, type AuthUser } from '../auth'

type Props = {
  onAuthed: (user: AuthUser) => void
}

export default function LoginPage({ onAuthed }: Props) {
  const [mode, setMode] = useState<'login' | 'register'>('login')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [password2, setPassword2] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  async function onSubmit(e: FormEvent) {
    e.preventDefault()
    setLoading(true)
    setError(null)
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
        <button className="btn" type="submit" disabled={loading}>
          {loading ? '提交中…' : mode === 'login' ? '登录' : '注册'}
        </button>
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
