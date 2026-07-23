export type Category = {
  id: string
  label: string
  count: number
}

export type InterfaceSummary = {
  name: string
  category: string
  category_label: string
  doc: string
  param_count: number
}

export type ParamInfo = {
  name: string
  required: boolean
  default: unknown
  annotation: string | null
}

export type InterfaceDetail = {
  source?: string
  name: string
  category: string
  category_label: string
  doc: string
  docstring: string
  params: ParamInfo[]
  example_params: Record<string, unknown>
}

export type FetchResult = {
  source?: string
  name: string
  params: Record<string, unknown>
  type: string
  columns: string[]
  rows: Record<string, unknown>[]
  total: number
  truncated: boolean
  returned: number
  raw?: unknown
}

export type SourceHealth = {
  id: string
  label: string
  ready: boolean
  version?: string
  interface_count?: number
  features?: string[]
  message?: string | null
  token_configured?: boolean
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    headers: { 'Content-Type': 'application/json', ...(init?.headers || {}) },
    ...init,
  })
  if (!res.ok) {
    let detail = res.statusText
    try {
      const body = await res.json()
      detail = body.detail || JSON.stringify(body)
    } catch {
      /* ignore */
    }
    throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail))
  }
  return res.json() as Promise<T>
}

export function getHealth() {
  return request<{ status: string; sources: SourceHealth[] }>('/api/health')
}

export function getSources() {
  return request<{
    sources: Array<{
      id: string
      label: string
      features: string[]
      docs_url?: string
      ready?: boolean
      message?: string | null
    }>
  }>('/api/sources')
}

export function getSourceHealth(source: string) {
  return request<SourceHealth>(`/api/${encodeURIComponent(source)}/health`)
}

export function getCategories(source: string) {
  return request<{ categories: Category[]; total: number }>(
    `/api/${encodeURIComponent(source)}/categories`,
  )
}

export function getInterfaces(
  source: string,
  category?: string | null,
  keyword?: string,
) {
  const qs = new URLSearchParams()
  if (category) qs.set('category', category)
  if (keyword) qs.set('keyword', keyword)
  const q = qs.toString()
  return request<{ interfaces: InterfaceSummary[]; count: number }>(
    `/api/${encodeURIComponent(source)}/interfaces${q ? `?${q}` : ''}`,
  )
}

export function getInterface(source: string, name: string) {
  return request<InterfaceDetail>(
    `/api/${encodeURIComponent(source)}/interfaces/${encodeURIComponent(name)}`,
  )
}

export function fetchData(
  source: string,
  name: string,
  params: Record<string, unknown>,
  limit = 500,
) {
  return request<FetchResult>(`/api/${encodeURIComponent(source)}/fetch`, {
    method: 'POST',
    body: JSON.stringify({ name, params, limit }),
  })
}
