const RESERVED = new Set(['api', 'category', 'q', 'view'])

export type UrlExplorerState = {
  api: string | null
  category: string | null
  q: string
  params: Record<string, string>
}

export function readUrlState(): UrlExplorerState {
  const sp = new URLSearchParams(window.location.search)
  const params: Record<string, string> = {}
  sp.forEach((value, key) => {
    if (!RESERVED.has(key)) {
      params[key] = value
    }
  })
  return {
    api: sp.get('api'),
    category: sp.get('category'),
    q: sp.get('q') ?? '',
    params,
  }
}

export function writeUrlState(state: {
  api: string | null
  category: string | null
  q: string
  params: Record<string, string>
}): void {
  const sp = new URLSearchParams()
  if (state.api) sp.set('api', state.api)
  if (state.category) sp.set('category', state.category)
  const q = state.q.trim()
  if (q) sp.set('q', q)

  for (const [key, value] of Object.entries(state.params)) {
    if (RESERVED.has(key)) continue
    if (value === '') continue
    sp.set(key, value)
  }

  const search = sp.toString()
  const next = `${window.location.pathname}${search ? `?${search}` : ''}`
  const current = `${window.location.pathname}${window.location.search}`
  if (next !== current) {
    window.history.replaceState(null, '', next)
  }
}
