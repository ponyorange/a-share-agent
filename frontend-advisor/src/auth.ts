const TOKEN_KEY = 'advisor_token'
const USER_KEY = 'advisor_user'
export const AUTH_CHANGED_EVENT = 'advisor-auth-changed'

export type AuthUser = {
  id: string
  username: string
  email?: string | null
  email_verified?: boolean
}

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY)
}

export function getUser(): AuthUser | null {
  const raw = localStorage.getItem(USER_KEY)
  if (!raw) return null
  try {
    return JSON.parse(raw) as AuthUser
  } catch {
    return null
  }
}

export function setSession(token: string, user: AuthUser) {
  localStorage.setItem(TOKEN_KEY, token)
  localStorage.setItem(USER_KEY, JSON.stringify(user))
}

export function clearSession() {
  localStorage.removeItem(TOKEN_KEY)
  localStorage.removeItem(USER_KEY)
  window.dispatchEvent(new Event(AUTH_CHANGED_EVENT))
}

export async function authFetch<T>(url: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers || {})
  const token = getToken()
  if (token) headers.set('Authorization', `Bearer ${token}`)
  if (init?.body && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json')
  }
  const res = await fetch(url, { ...init, headers })
  if (res.status === 401) {
    clearSession()
    throw new Error('请先登录')
  }
  if (!res.ok) {
    let detail = res.statusText
    try {
      const body = (await res.json()) as { detail?: string }
      if (body.detail) detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail)
    } catch {
      /* ignore */
    }
    throw new Error(detail || `HTTP ${res.status}`)
  }
  return res.json() as Promise<T>
}

export function login(username: string, password: string) {
  return authFetch<{ token: string; user: AuthUser }>('/api/auth/login', {
    method: 'POST',
    body: JSON.stringify({ username, password }),
  })
}

export function register(username: string, password: string, password2: string) {
  return authFetch<{ token: string; user: AuthUser }>('/api/auth/register', {
    method: 'POST',
    body: JSON.stringify({ username, password, password2 }),
  })
}

export function fetchMe() {
  return authFetch<{ user: AuthUser }>('/api/auth/me')
}

export function sendEmailBindCode(email: string) {
  return authFetch<{ ok: boolean; message: string }>('/api/auth/account/email/send-code', {
    method: 'POST',
    body: JSON.stringify({ email }),
  })
}

export function verifyEmailBind(email: string, code: string) {
  return authFetch<{ ok: boolean; user: AuthUser }>('/api/auth/account/email/verify', {
    method: 'POST',
    body: JSON.stringify({ email, code }),
  })
}

export function changePassword(oldPassword: string, newPassword: string) {
  return authFetch<{ ok: boolean; message: string }>('/api/auth/account/password', {
    method: 'POST',
    body: JSON.stringify({ old_password: oldPassword, new_password: newPassword }),
  })
}

export function sendPasswordResetCode(account: string) {
  return authFetch<{ ok: boolean; message: string }>('/api/auth/password-reset/send-code', {
    method: 'POST',
    body: JSON.stringify({ account }),
  })
}

export function confirmPasswordReset(account: string, code: string, newPassword: string) {
  return authFetch<{ ok: boolean; message: string }>('/api/auth/password-reset/confirm', {
    method: 'POST',
    body: JSON.stringify({
      account,
      code,
      new_password: newPassword,
    }),
  })
}
