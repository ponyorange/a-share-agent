import type { LlmProviderId, LlmSlotId } from './agentApi'

export const PROVIDER_META: {
  id: LlmProviderId
  label: string
  docs: string
  docsLabel: string
  defaultModel: string
}[] = [
  {
    id: 'deepseek',
    label: 'DeepSeek',
    docs: 'https://api-docs.deepseek.com/zh-cn/',
    docsLabel: 'DeepSeek API',
    defaultModel: 'deepseek-v4-flash',
  },
  {
    id: 'kimi',
    label: 'Kimi',
    docs: 'https://platform.kimi.com/docs/api/overview',
    docsLabel: 'Kimi API',
    defaultModel: 'kimi-k2.6',
  },
  {
    id: 'qwen',
    label: '千问',
    docs: 'https://platform.qianwenai.com/docs/developer-guides/getting-started/text-generation-models',
    docsLabel: '千问 API',
    defaultModel: 'qwen3.7-plus',
  },
]

export const SLOT_ROWS: { id: LlmSlotId; label: string }[] = [
  { id: 'agent', label: '主 Agent 对话' },
  { id: 'paper', label: '模拟盘' },
  { id: 'home', label: '首页解读' },
  { id: 'monitor', label: '定时任务' },
  { id: 'policy', label: '政策雷达' },
  { id: 'limitup', label: '打板晋级' },
  { id: 'committee_quick', label: '委员会·快速' },
  { id: 'committee_deep', label: '委员会·深度' },
]

export function filterProviderModels(
  available: { id: string }[],
  enabled: string[],
  query: string,
): { id: string }[] {
  const source = available.length ? available : enabled.map((id) => ({ id }))
  const q = query.trim().toLowerCase()
  const filtered = q
    ? source.filter((m) => m.id.toLowerCase().includes(q))
    : [...source]
  const enabledSet = new Set(enabled)
  const selected = enabled
    .filter((id) => filtered.some((m) => m.id === id))
    .map((id) => ({ id }))
  const rest = filtered.filter((m) => !enabledSet.has(m.id))
  return [...selected, ...rest]
}

export function clampSlotModel(
  model: string | undefined,
  enabled: string[],
): string {
  if (model && enabled.includes(model)) return model
  return enabled[0] || model || ''
}
