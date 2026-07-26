import { authFetch, getToken } from './auth'

export type LlmSettings = {
  configured: boolean
  provider?: string
  model?: string
  base_url?: string
  key_hint?: string | null
  last_validated_at?: string | null
  web_research_enabled?: boolean
  tavily_enabled?: boolean
  tavily_configured?: boolean
  tavily_key_hint?: string | null
  tavily_validated_at?: string | null
}

export function fetchLlmSettings(): Promise<LlmSettings> {
  return authFetch('/api/advisor/llm/settings')
}

export function saveLlmSettings(body: {
  api_key?: string
  model?: string
  base_url?: string
  web_research_enabled?: boolean
  tavily_enabled?: boolean
  tavily_api_key?: string
}): Promise<LlmSettings> {
  return authFetch('/api/advisor/llm/settings', {
    method: 'PUT',
    body: JSON.stringify(body),
  })
}

export function clearLlmSettings(): Promise<LlmSettings> {
  return authFetch('/api/advisor/llm/settings', { method: 'DELETE' })
}

export function clearTavilySettings(): Promise<LlmSettings> {
  return authFetch('/api/advisor/llm/settings/tavily', { method: 'DELETE' })
}

export type AgentChatResult = {
  session_id?: string
  reply: string
  tool_trace?: { tool: string; content: string }[]
  disclaimer?: string
}

export type SubagentProgress = {
  phase: 'data_agent' | 'main_agent'
  step:
    | 'delegate'
    | 'list_sources'
    | 'search'
    | 'describe'
    | 'fetch'
    | 'sandbox'
    | 'submit'
    | 'run_python'
    | 'web_research'
    | 'web_search'
    | 'fetch_url'
  status: 'started' | 'completed' | 'failed'
  message: string
  source?: string
  interface?: string
  rows?: number
  truncated?: boolean
  error_code?: string
}

export type AgentSession = {
  session_id: string
  title: string
  updated_at?: string
  message_count?: number
}

export type AgentStoredMessage = {
  role: string
  content: string
  tool_trace?: { tool: string; content: string }[]
  created_at?: string
}

export function listAgentSessions(limit = 20): Promise<{ sessions: AgentSession[] }> {
  return authFetch(`/api/advisor/agent/sessions?limit=${limit}`)
}

export function createAgentSession(): Promise<{ session_id: string }> {
  return authFetch('/api/advisor/agent/sessions', { method: 'POST', body: '{}' })
}

export function fetchAgentMessages(
  sessionId: string,
): Promise<{ session_id: string; messages: AgentStoredMessage[] }> {
  return authFetch(`/api/advisor/agent/sessions/${encodeURIComponent(sessionId)}/messages`)
}

export function deleteAgentSession(sessionId: string) {
  return authFetch(`/api/advisor/agent/sessions/${encodeURIComponent(sessionId)}`, {
    method: 'DELETE',
  })
}

/** @deprecated 优先用 streamAgentChat */
export function agentChat(
  message: string,
  history: { role: string; content: string }[] = [],
  sessionId?: string | null,
): Promise<AgentChatResult> {
  return authFetch('/api/advisor/agent/chat', {
    method: 'POST',
    body: JSON.stringify({ message, history, session_id: sessionId || undefined }),
  })
}

/** SSE：meta → (tool | subagent_progress)* → token* → done */
export async function streamAgentChat(
  message: string,
  sessionId: string | null | undefined,
  handlers: {
    onMeta?: (meta: { session_id: string; context_messages?: number }) => void
    onTool?: (row: { tool: string; content: string }) => void
    onSubagentProgress?: (progress: SubagentProgress) => void
    onToken?: (delta: string) => void
    onDone?: (data: AgentChatResult) => void
    onError?: (detail: string) => void
  },
  signal?: AbortSignal,
): Promise<void> {
  const token = getToken()
  const res = await fetch('/api/advisor/agent/chat/stream', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify({
      message,
      session_id: sessionId || undefined,
    }),
    signal,
  })
  if (res.status === 401) {
    handlers.onError?.('请先登录')
    return
  }
  if (!res.ok || !res.body) {
    handlers.onError?.(`HTTP ${res.status}`)
    return
  }

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buf = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buf += decoder.decode(value, { stream: true })
    const parts = buf.split('\n\n')
    buf = parts.pop() || ''
    for (const chunk of parts) {
      let eventName = 'message'
      let dataLine = ''
      for (const line of chunk.split('\n')) {
        if (line.startsWith('event:')) eventName = line.slice(6).trim()
        else if (line.startsWith('data:')) dataLine += line.slice(5).trim()
      }
      if (!dataLine) continue
      let data: Record<string, unknown>
      try {
        data = JSON.parse(dataLine) as Record<string, unknown>
      } catch {
        continue
      }
      if (eventName === 'meta') {
        handlers.onMeta?.(data as { session_id: string; context_messages?: number })
      } else if (eventName === 'tool') {
        handlers.onTool?.(data as { tool: string; content: string })
      } else if (eventName === 'subagent_progress') {
        handlers.onSubagentProgress?.(data as SubagentProgress)
      } else if (eventName === 'token') {
        handlers.onToken?.(String(data.delta || ''))
      } else if (eventName === 'done') {
        handlers.onDone?.(data as AgentChatResult)
      } else if (eventName === 'error') {
        handlers.onError?.(String(data.detail || 'Agent 失败'))
      }
    }
  }
}

export function agentApplyStrategy(
  configPatch: Record<string, unknown>,
  notes?: string,
) {
  return authFetch('/api/advisor/agent/strategy/apply', {
    method: 'POST',
    body: JSON.stringify({
      config_patch: configPatch,
      confirm: true,
      notes,
    }),
  })
}

export type KnowledgeMode = 'always' | 'on_demand'

export type KnowledgeItem = {
  id: string
  title: string
  mode: KnowledgeMode
  enabled: boolean
  description: string
  body: string
  created_at?: string | null
  updated_at?: string | null
}

export type KnowledgeInput = {
  title: string
  mode: KnowledgeMode
  enabled: boolean
  description: string
  body: string
}

export type AgentSystemPrompt = {
  system_prompt: string
  updated_at?: string | null
}

export function fetchAgentSystemPrompt(): Promise<AgentSystemPrompt> {
  return authFetch('/api/advisor/agent-config/system-prompt')
}

export function saveAgentSystemPrompt(
  system_prompt: string,
): Promise<AgentSystemPrompt> {
  return authFetch('/api/advisor/agent-config/system-prompt', {
    method: 'PUT',
    body: JSON.stringify({ system_prompt }),
  })
}

export function listKnowledge(): Promise<{ items: KnowledgeItem[] }> {
  return authFetch('/api/advisor/knowledge')
}

export function createKnowledge(body: KnowledgeInput): Promise<KnowledgeItem> {
  return authFetch('/api/advisor/knowledge', {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

export function updateKnowledge(
  id: string,
  body: KnowledgeInput,
): Promise<KnowledgeItem> {
  return authFetch(`/api/advisor/knowledge/${encodeURIComponent(id)}`, {
    method: 'PUT',
    body: JSON.stringify(body),
  })
}

export function deleteKnowledge(id: string): Promise<{ ok: boolean }> {
  return authFetch(`/api/advisor/knowledge/${encodeURIComponent(id)}`, {
    method: 'DELETE',
  })
}
