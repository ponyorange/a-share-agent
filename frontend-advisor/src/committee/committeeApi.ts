import { clearSession, getToken } from '../auth'

const BASE = '/api/advisor/committee'

export type CommitteeRunStatus =
  | 'created'
  | 'queued'
  | 'running'
  | 'pending'
  | 'collecting'
  | 'analyzing'
  | 'debating'
  | 'proposing'
  | 'backtesting'
  | 'risk_review'
  | 'completed'
  | 'failed'
  | 'cancelled'

export type CommitteeRun = {
  user_id: string
  run_id: string
  status: CommitteeRunStatus
  version: number
  strategy_version: string
  horizon: 'next_day' | 'next_week' | 'next_month'
  universe: string[]
  as_of: string
  snapshot_id?: string | null
  created_at: string
  updated_at: string
  started_at?: string | null
  completed_at?: string | null
  error_code?: string | null
  error_message?: string | null
  idempotency_key?: string | null
  request_hash?: string | null
  queue_job_id?: string | null
  parent_run_id?: string | null
  attempt: number
  cancel_requested?: boolean
  initial_input?: Record<string, unknown>
  job_heartbeat_at?: string | null
  job_deadline_at?: string | null
  next_attempt?: number
  execution_owner?: string | null
  execution_lease_expires_at?: string | null
  execution_heartbeat_at?: string | null
}

export type CommitteeEventRecord = {
  event_id: string
  sequence: number
  event_type: string
  payload: Record<string, unknown>
  created_at?: string
  attempt?: number
  node?: string
}

export type CommitteeArtifact = {
  artifact_id: string
  user_id?: string
  run_id?: string
  kind: string
  payload: unknown
  created_at?: string
  attempt?: number
  node?: string
}

export type CommitteeRunDetail = {
  run: CommitteeRun
  artifacts: CommitteeArtifact[]
  events: CommitteeEventRecord[]
}

export type CommitteeRunCreate = {
  symbols: string[]
  boards: Array<'etf' | 'hs' | 'star'>
  horizon: 'next_day'
  strategy_version: string
}

export type PlannedOrder = {
  symbol: string
  side: string
  qty: number
  price: number
  name?: string | null
}

export type ApprovalPreview = {
  proposal_hash: string
  decision_hash: string
  account_version: number
  orders: PlannedOrder[]
}

export type ApprovalBody = {
  preview_id: string
  decision_hash: string
  proposal_hash: string
  account_version: number
  confirm: true
}

export class CommitteeApiError extends Error {
  readonly status: number

  constructor(message: string, status: number) {
    super(message)
    this.name = 'CommitteeApiError'
    this.status = status
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers)
  const token = getToken()
  if (token) headers.set('Authorization', `Bearer ${token}`)
  if (init.body && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json')
  const response = await fetch(`${BASE}${path}`, { ...init, headers })
  if (response.status === 401) {
    clearSession()
    throw new CommitteeApiError('请先登录', 401)
  }
  if (!response.ok) {
    let detail = response.statusText || `HTTP ${response.status}`
    try {
      const body = (await response.json()) as { detail?: unknown }
      if (body.detail) {
        detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail)
      }
    } catch {
      // 保留 HTTP 信息
    }
    throw new CommitteeApiError(detail, response.status)
  }
  return response.json() as Promise<T>
}

function idempotencyHeaders(key: string) {
  return { 'Idempotency-Key': key }
}

export function createCommitteeRun(body: CommitteeRunCreate, idempotencyKey: string) {
  return request<{ run_id: string; status: CommitteeRunStatus }>('/runs', {
    method: 'POST',
    headers: idempotencyHeaders(idempotencyKey),
    body: JSON.stringify(body),
  })
}

export function listCommitteeRuns(limit = 50) {
  return request<{ runs: CommitteeRun[] }>(`/runs?limit=${limit}`)
}

export function getCommitteeRun(runId: string, signal?: AbortSignal) {
  return request<CommitteeRunDetail>(
    `/runs/${encodeURIComponent(runId)}`,
    { signal },
  )
}

export function deleteCommitteeRun(runId: string, signal?: AbortSignal) {
  return request<{ run_id: string; deleted: true }>(
    `/runs/${encodeURIComponent(runId)}`,
    { method: 'DELETE', signal },
  )
}

export function cancelCommitteeRun(runId: string, signal?: AbortSignal) {
  return request<{ run_id: string; status: CommitteeRunStatus }>(
    `/runs/${encodeURIComponent(runId)}/cancel`,
    { method: 'POST', signal },
  )
}

export function retryCommitteeRun(runId: string, idempotencyKey: string) {
  return request<{ run_id: string; status: CommitteeRunStatus }>(
    `/runs/${encodeURIComponent(runId)}/retry`,
    { method: 'POST', headers: idempotencyHeaders(idempotencyKey) },
  )
}

export function getCommitteeOrderPreview(runId: string, signal?: AbortSignal) {
  return request<{ preview: ApprovalPreview }>(
    `/runs/${encodeURIComponent(runId)}/order-preview`,
    { signal },
  )
}

export function bindCommitteeOrderPreview(
  runId: string,
  preview: ApprovalPreview,
  signal?: AbortSignal,
) {
  return request<{ preview_id: string; preview: ApprovalPreview }>(
    `/runs/${encodeURIComponent(runId)}/order-preview`,
    {
      method: 'POST',
      body: JSON.stringify({
        decision_hash: preview.decision_hash,
        account_version: preview.account_version,
      }),
      signal,
    },
  )
}

export function approveCommitteeRun(
  runId: string,
  body: ApprovalBody,
  idempotencyKey: string,
  signal?: AbortSignal,
) {
  return request<{ approval: Record<string, unknown>; replayed: boolean }>(
    `/runs/${encodeURIComponent(runId)}/approve`,
    {
      method: 'POST',
      headers: idempotencyHeaders(idempotencyKey),
      body: JSON.stringify(body),
      signal,
    },
  )
}

export type ParsedSseEvent = {
  id?: string
  event: string
  data: Record<string, unknown>
}

export function createSseParser(onEvent: (event: ParsedSseEvent) => void) {
  let buffer = ''
  let pendingCarriageReturn = false

  function parseBlock(block: string) {
    let id: string | undefined
    let event = 'message'
    const data: string[] = []
    for (const rawLine of block.split('\n')) {
      const line = rawLine.endsWith('\r') ? rawLine.slice(0, -1) : rawLine
      if (!line || line.startsWith(':')) continue
      const separator = line.indexOf(':')
      const field = separator < 0 ? line : line.slice(0, separator)
      let value = separator < 0 ? '' : line.slice(separator + 1)
      if (value.startsWith(' ')) value = value.slice(1)
      if (field === 'id') id = value
      else if (field === 'event') event = value || 'message'
      else if (field === 'data') data.push(value)
    }
    if (!data.length) return
    try {
      onEvent({ id, event, data: JSON.parse(data.join('\n')) as Record<string, unknown> })
    } catch {
      // 服务端事件必须是 JSON；损坏帧留待重连恢复
    }
  }

  function drain(final = false) {
    let boundary = buffer.indexOf('\n\n')
    while (boundary >= 0) {
      parseBlock(buffer.slice(0, boundary))
      buffer = buffer.slice(boundary + 2)
      boundary = buffer.indexOf('\n\n')
    }
    if (final && buffer.trim()) {
      parseBlock(buffer)
      buffer = ''
    }
  }

  return {
    push(chunk: string) {
      let normalized = chunk
      if (pendingCarriageReturn) {
        buffer += '\n'
        if (normalized.startsWith('\n')) normalized = normalized.slice(1)
        pendingCarriageReturn = false
      }
      if (normalized.endsWith('\r') && !normalized.endsWith('\r\r')) {
        pendingCarriageReturn = true
        normalized = normalized.slice(0, -1)
      }
      buffer += normalized.replace(/\r\n/g, '\n').replace(/\r/g, '\n')
      drain()
    },
    finish() {
      if (pendingCarriageReturn) {
        buffer += '\n'
        pendingCarriageReturn = false
      }
      drain(true)
    },
  }
}

const TERMINAL_EVENTS = new Set(['completed', 'failed', 'cancelled'])

export type StreamCommitteeOptions = {
  signal?: AbortSignal
  lastEventId?: string
  reconnect?: {
    initialDelayMs?: number
    maxDelayMs?: number
    maxAttempts?: number
  }
  sleep?: (delayMs: number, signal?: AbortSignal) => Promise<void>
}

export function waitForRetry(delayMs: number, signal?: AbortSignal) {
  return new Promise<void>((resolve, reject) => {
    const onAbort = () => {
      window.clearTimeout(timer)
      signal?.removeEventListener('abort', onAbort)
      reject(signal?.reason)
    }
    const timer = window.setTimeout(() => {
      signal?.removeEventListener('abort', onAbort)
      resolve()
    }, delayMs)
    if (signal?.aborted) {
      onAbort()
      return
    }
    signal?.addEventListener('abort', onAbort, { once: true })
  })
}

function sequenceHeader(id?: string) {
  return id?.split('-', 1)[0]
}

function retryAfterMs(response: Response) {
  const value = response.headers.get('Retry-After')
  if (!value) return undefined
  const seconds = Number(value)
  if (Number.isFinite(seconds) && seconds >= 0) return seconds * 1000
  const date = Date.parse(value)
  return Number.isFinite(date) ? Math.max(0, date - Date.now()) : undefined
}

function isRetryableStatus(status: number) {
  return status === 408 || status === 429 || status >= 500
}

export async function streamCommitteeEvents(
  runId: string,
  handlers: {
    onEvent: (event: ParsedSseEvent) => void
    onError?: (error: Error) => void
    onReconnect?: (attempt: number, delayMs: number) => void
    onUnauthorized?: () => void
  },
  options: StreamCommitteeOptions = {},
): Promise<void> {
  const initialDelay = options.reconnect?.initialDelayMs ?? 500
  const maxDelay = options.reconnect?.maxDelayMs ?? 8_000
  const maxAttempts = options.reconnect?.maxAttempts ?? Number.POSITIVE_INFINITY
  const sleep = options.sleep ?? waitForRetry
  let attempts = 0
  let lastId = options.lastEventId

  while (!options.signal?.aborted) {
    let terminal = false
    let retryDelay: number | undefined
    try {
      const headers = new Headers({ Accept: 'text/event-stream' })
      const token = getToken()
      if (token) headers.set('Authorization', `Bearer ${token}`)
      const resume = sequenceHeader(lastId)
      if (resume) headers.set('Last-Event-ID', resume)
      const response = await fetch(`${BASE}/runs/${encodeURIComponent(runId)}/events`, {
        headers,
        signal: options.signal,
      })
      if (!response.ok) {
        const error = new CommitteeApiError(`事件流 HTTP ${response.status}`, response.status)
        if (response.status === 401) {
          clearSession()
          handlers.onError?.(error)
          handlers.onUnauthorized?.()
          return
        }
        if (!isRetryableStatus(response.status)) {
          handlers.onError?.(error)
          return
        }
        retryDelay = retryAfterMs(response)
        throw error
      }
      if (!response.body) {
        throw new CommitteeApiError('事件流响应为空', response.status)
      }
      const decoder = new TextDecoder()
      const parser = createSseParser((event) => {
        if (terminal) return
        if (event.id) lastId = event.id
        handlers.onEvent(event)
        terminal ||= TERMINAL_EVENTS.has(event.event)
      })
      const reader = response.body.getReader()
      while (!terminal) {
        const result = await reader.read()
        if (result.done) break
        parser.push(decoder.decode(result.value, { stream: true }))
      }
      parser.push(decoder.decode())
      parser.finish()
      if (terminal) {
        await reader.cancel()
        return
      }
      // Stream ended without a terminal frame — confirm run status before reconnecting.
      try {
        const detail = await getCommitteeRun(runId, options.signal)
        if (TERMINAL_EVENTS.has(detail.run.status)) {
          handlers.onEvent({
            id: `${detail.run.status}-terminal`,
            event: detail.run.status,
            data: {
              status: detail.run.status,
              error_code: detail.run.error_code ?? null,
              error_message: detail.run.error_message ?? null,
            },
          })
          return
        }
      } catch (statusError) {
        if (
          options.signal?.aborted ||
          (statusError instanceof DOMException && statusError.name === 'AbortError')
        ) {
          return
        }
        handlers.onError?.(
          statusError instanceof Error ? statusError : new Error(String(statusError)),
        )
      }
    } catch (error) {
      if (options.signal?.aborted || (error instanceof DOMException && error.name === 'AbortError')) {
        return
      }
      handlers.onError?.(error instanceof Error ? error : new Error(String(error)))
    }
    if (options.signal?.aborted || attempts >= maxAttempts) return
    const delay = retryDelay ?? Math.min(maxDelay, initialDelay * 2 ** attempts)
    attempts += 1
    handlers.onReconnect?.(attempts, delay)
    try {
      await sleep(delay, options.signal)
    } catch {
      return
    }
  }
}
