import type {
  CommitteeArtifact,
  CommitteeEventRecord,
  ParsedSseEvent,
} from './committeeApi'

export type CommitteeChatRole =
  | 'data'
  | 'fundamental'
  | 'technical'
  | 'news'
  | 'quant'
  | 'bull'
  | 'bear'
  | 'trader'
  | 'backtest'
  | 'risk'
  | 'chair'

export type CommitteeChatStatus =
  | 'streaming'
  | 'completed'
  | 'degraded'
  | 'failed'

export type CommitteeChatMessage = {
  message_id: string
  role: CommitteeChatRole
  node: string
  round?: number | null
  content: string
  status: CommitteeChatStatus
  sequence: number
  generation: number
  created_at?: string | null
  completed_at?: string | null
  card_kind?: string | null
  card_ref?: { attempt: number; node: string; kind: string } | null
  nextOffset: number
  revealing?: boolean
}

export type ChatMessagesState = {
  order: string[]
  byId: Record<string, CommitteeChatMessage>
}

export const ROLE_META: Record<
  CommitteeChatRole,
  { label: string; tone: string }
> = {
  data: { label: '数据助手', tone: 'system' },
  fundamental: { label: '基本面分析师', tone: 'analyst' },
  technical: { label: '技术分析师', tone: 'analyst' },
  news: { label: '新闻分析师', tone: 'analyst' },
  quant: { label: '量化分析师', tone: 'analyst' },
  bull: { label: '多方辩手', tone: 'debate' },
  bear: { label: '空方辩手', tone: 'debate' },
  trader: { label: '交易员', tone: 'trader' },
  backtest: { label: '回测员', tone: 'system' },
  risk: { label: '风控官', tone: 'system' },
  chair: { label: '主席', tone: 'chair' },
}

const ROLE_ORDER: CommitteeChatRole[] = [
  'data',
  'fundamental',
  'technical',
  'news',
  'quant',
  'bull',
  'bear',
  'trader',
  'backtest',
  'risk',
  'chair',
]

const ROLE_INDEX = new Map(ROLE_ORDER.map((role, index) => [role, index]))
const CHAT_ROLES = new Set<CommitteeChatRole>(ROLE_ORDER)
const FINAL_STATUSES = new Set<CommitteeChatStatus>([
  'completed',
  'degraded',
  'failed',
])

export const initialChatMessagesState: ChatMessagesState = {
  order: [],
  byId: {},
}

export function codePointLength(text: string): number {
  return [...text].length
}

type HistoryContext = {
  runId: string
  attempt: number
}

export type ChatMessagesAction =
  | { type: 'reset' }
  | {
      type: 'hydrate'
      events?: CommitteeEventRecord[]
      artifacts?: CommitteeArtifact[]
      messages?: CommitteeChatMessage[]
      context?: HistoryContext
    }
  | {
      type: 'merge'
      events?: CommitteeEventRecord[]
      artifacts?: CommitteeArtifact[]
      messages?: CommitteeChatMessage[]
      context?: HistoryContext
    }
  | { type: 'sse'; event: ParsedSseEvent }
  | {
      type: 'revealCompleted'
      message: CommitteeChatMessage
      visibleCodePoints: number
    }
  | { type: 'interruptStreaming' }

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function stringValue(value: unknown): string | undefined {
  return typeof value === 'string' ? value : undefined
}

function numberValue(value: unknown): number | undefined {
  return typeof value === 'number' && Number.isFinite(value) ? value : undefined
}

function roleValue(value: unknown): CommitteeChatRole | undefined {
  return typeof value === 'string' && CHAT_ROLES.has(value as CommitteeChatRole)
    ? value as CommitteeChatRole
    : undefined
}

function statusValue(value: unknown): CommitteeChatStatus | undefined {
  return typeof value === 'string'
    && FINAL_STATUSES.has(value as CommitteeChatStatus)
    ? value as CommitteeChatStatus
    : undefined
}

function sequenceFromSseId(id?: string) {
  if (!id) return undefined
  const sequence = Number(id.split('-', 1)[0])
  return Number.isInteger(sequence) && sequence >= 0 ? sequence : undefined
}

function fallbackSequence(state: ChatMessagesState) {
  return Object.values(state.byId).reduce(
    (largest, message) => Math.max(largest, message.sequence),
    -1,
  ) + 1
}

function compareMessages(
  left: CommitteeChatMessage,
  right: CommitteeChatMessage,
) {
  return left.sequence - right.sequence
    || (ROLE_INDEX.get(left.role) ?? ROLE_ORDER.length)
      - (ROLE_INDEX.get(right.role) ?? ROLE_ORDER.length)
    || left.message_id.localeCompare(right.message_id)
}

function stateFromMessages(messages: CommitteeChatMessage[]): ChatMessagesState {
  const byId: Record<string, CommitteeChatMessage> = {}
  for (const message of messages) byId[message.message_id] = message
  const order = Object.values(byId).sort(compareMessages).map(
    (message) => message.message_id,
  )
  return { order, byId }
}

function withMessage(
  state: ChatMessagesState,
  message: CommitteeChatMessage,
): ChatMessagesState {
  return stateFromMessages([
    ...Object.values(state.byId).filter(
      (current) => current.message_id !== message.message_id,
    ),
    message,
  ])
}

function completedMessage(
  payload: Record<string, unknown>,
  sequence: number,
  fallbackCreatedAt?: string,
): CommitteeChatMessage | undefined {
  const messageId = stringValue(payload.message_id)
  const role = roleValue(payload.role)
  const node = stringValue(payload.node)
  const content = stringValue(payload.content)
  if (!messageId || !role || !node || content === undefined) return undefined

  const cardRef = isRecord(payload.card_ref)
    && numberValue(payload.card_ref.attempt) !== undefined
    && stringValue(payload.card_ref.node)
    && stringValue(payload.card_ref.kind)
    ? {
        attempt: numberValue(payload.card_ref.attempt)!,
        node: stringValue(payload.card_ref.node)!,
        kind: stringValue(payload.card_ref.kind)!,
      }
    : null

  return {
    message_id: messageId,
    role,
    node,
    round: numberValue(payload.round) ?? null,
    content,
    status: statusValue(payload.status) ?? 'completed',
    sequence,
    generation: numberValue(payload.generation) ?? 1,
    created_at: stringValue(payload.created_at) ?? fallbackCreatedAt ?? null,
    completed_at: stringValue(payload.completed_at) ?? fallbackCreatedAt ?? null,
    card_kind: stringValue(payload.card_kind) ?? null,
    card_ref: cardRef,
    nextOffset: codePointLength(content),
  }
}

export function messagesFromEvents(
  events: CommitteeEventRecord[],
): CommitteeChatMessage[] {
  const byId = new Map<string, CommitteeChatMessage>()
  const orderedEvents = [...events].sort(
    (left, right) => left.sequence - right.sequence
      || left.event_id.localeCompare(right.event_id),
  )
  for (const event of orderedEvents) {
    if (event.event_type !== 'message_completed') continue
    const message = completedMessage(
      event.payload,
      event.sequence,
      event.created_at,
    )
    if (message) byId.set(message.message_id, message)
  }
  return [...byId.values()].sort(compareMessages)
}

function legacyMessage(
  artifact: CommitteeArtifact,
  context: HistoryContext,
  sequence: number,
  suffix: string,
  role: CommitteeChatRole,
  content: string,
  cardKind: string,
  round?: number | null,
): CommitteeChatMessage {
  const attempt = artifact.attempt ?? context.attempt
  const node = role === 'data' ? 'prepare' : role
  return {
    message_id: `legacy:${context.runId}:${attempt}:${artifact.artifact_id}:${suffix}`,
    role,
    node,
    round: round ?? null,
    content,
    status: 'completed',
    sequence,
    generation: 1,
    created_at: artifact.created_at ?? null,
    completed_at: artifact.created_at ?? null,
    card_kind: cardKind,
    card_ref: { attempt, node, kind: cardKind },
    nextOffset: codePointLength(content),
  }
}

function percent(value: unknown) {
  const numeric = numberValue(value)
  return numeric === undefined ? undefined : `${Math.round(numeric * 100)}%`
}

export function messagesFromArtifacts(
  artifacts: CommitteeArtifact[],
  context: HistoryContext,
): CommitteeChatMessage[] {
  const messages: CommitteeChatMessage[] = []
  let sequence = 0
  const append = (
    artifact: CommitteeArtifact,
    suffix: string,
    role: CommitteeChatRole,
    content: string,
    cardKind = artifact.kind,
    round?: number | null,
  ) => {
    messages.push(legacyMessage(
      artifact,
      context,
      sequence,
      suffix,
      role,
      content,
      cardKind,
      round,
    ))
    sequence += 1
  }

  for (const artifact of artifacts) {
    const payload = artifact.payload
    if (artifact.kind === 'analyst_reports' && Array.isArray(payload)) {
      payload.forEach((item, index) => {
        if (!isRecord(item)) return
        const role = roleValue(item.role)
        const thesis = stringValue(item.thesis)
        if (!role || !thesis) return
        append(artifact, String(index), role, thesis)
      })
    } else if (artifact.kind === 'debate_turns' && Array.isArray(payload)) {
      payload.forEach((item, index) => {
        if (!isRecord(item)) return
        const role = roleValue(item.speaker)
        const argument = stringValue(item.argument)
        if (!role || !argument) return
        append(
          artifact,
          String(index),
          role,
          argument,
          artifact.kind,
          numberValue(item.sequence) ?? null,
        )
      })
    } else if (
      (artifact.kind === 'trade_proposal' || artifact.kind === 'trade_proposals')
      && (isRecord(payload) || Array.isArray(payload))
    ) {
      const proposals = Array.isArray(payload) ? payload : [payload]
      proposals.forEach((item, index) => {
        if (!isRecord(item)) return
        const rationale = stringValue(item.rationale)
        if (!rationale) return
        const direction = stringValue(item.direction)
        const weight = percent(item.target_weight)
        const prefix = [direction, weight].filter(Boolean).join('，')
        append(
          artifact,
          String(index),
          'trader',
          prefix ? `${prefix}。${rationale}` : rationale,
          artifact.kind,
        )
      })
    } else if (artifact.kind === 'backtest_verdict' && isRecord(payload)) {
      const summary = stringValue(payload.summary)
      if (!summary) continue
      const result = payload.passed === true ? '通过' : '未通过'
      const score = numberValue(payload.score)
      append(
        artifact,
        '0',
        'backtest',
        `回测${result}${score === undefined ? '' : `，得分 ${score.toFixed(2)}`}。${summary}`,
      )
    } else if (artifact.kind === 'risk_verdict' && isRecord(payload)) {
      const status = stringValue(payload.status)
      const weight = percent(payload.approved_weight)
      const reasons = Array.isArray(payload.reasons)
        ? payload.reasons.filter((reason): reason is string => typeof reason === 'string')
        : []
      const content = [
        `风控结论：${status ?? '未知'}${weight ? `，批准仓位 ${weight}` : ''}。`,
        reasons.join('；'),
      ].filter(Boolean).join('')
      append(artifact, '0', 'risk', content)
    } else if (artifact.kind === 'final_decision' && isRecord(payload)) {
      const rationale = stringValue(payload.rationale)
      if (!rationale) continue
      const action = stringValue(payload.action)
      const symbol = stringValue(payload.symbol)
      const weight = percent(payload.target_weight)
      const summary = [symbol, action, weight].filter(Boolean).join('，')
      append(
        artifact,
        '0',
        'chair',
        summary ? `${summary}。${rationale}` : rationale,
      )
    } else if (artifact.kind === 'snapshot' && isRecord(payload)) {
      const universe = Array.isArray(payload.universe) ? payload.universe.length : 0
      append(artifact, '0', 'data', `已冻结 ${universe} 个标的的市场快照。`)
    }
  }

  return messages.sort(compareMessages)
}

export function applyChatSseEvent(
  state: ChatMessagesState,
  event: ParsedSseEvent,
): ChatMessagesState {
  if (
    event.event !== 'message_started'
    && event.event !== 'message_delta'
    && event.event !== 'message_completed'
  ) {
    return state
  }

  const payload = event.data
  const messageId = stringValue(payload.message_id)
  if (!messageId) return state
  const current = state.byId[messageId]
  const generation = numberValue(payload.generation) ?? 1

  if (event.event === 'message_started') {
    const role = roleValue(payload.role)
    const node = stringValue(payload.node)
    if (!role || !node || (current && generation < current.generation)) return state
    const sequence = sequenceFromSseId(event.id)
      ?? current?.sequence
      ?? fallbackSequence(state)
    return withMessage(state, {
      message_id: messageId,
      role,
      node,
      round: numberValue(payload.round) ?? null,
      content: '',
      status: 'streaming',
      sequence,
      generation,
      created_at: stringValue(payload.created_at) ?? current?.created_at ?? null,
      completed_at: null,
      card_kind: stringValue(payload.card_kind) ?? null,
      card_ref: null,
      nextOffset: 0,
    })
  }

  if (event.event === 'message_delta') {
    const delta = stringValue(payload.delta)
    const offset = numberValue(payload.offset)
    if (
      !current
      || current.status !== 'streaming'
      || generation !== current.generation
      || delta === undefined
      || offset !== codePointLength(current.content)
    ) {
      return state
    }
    const content = current.content + delta
    return withMessage(state, {
      ...current,
      content,
      nextOffset: codePointLength(content),
    })
  }

  const sequence = sequenceFromSseId(event.id)
    ?? current?.sequence
    ?? fallbackSequence(state)
  const completed = completedMessage(payload, sequence)
  return completed ? withMessage(state, completed) : state
}

function mergeMessages(
  state: ChatMessagesState,
  incoming: CommitteeChatMessage[],
) {
  let next = state
  for (const message of incoming) {
    const current = next.byId[message.message_id]
    if (
      !current
      || (message.status !== 'streaming' && !current.revealing)
    ) {
      next = withMessage(next, message)
    }
  }
  return next
}

function historySlot(message: CommitteeChatMessage) {
  return `${message.role}:${message.round ?? 0}`
}

function mergeHistoryMessages(
  eventMessages: CommitteeChatMessage[],
  suppliedMessages: CommitteeChatMessage[],
  artifactMessages: CommitteeChatMessage[],
) {
  const authoritative = [...suppliedMessages, ...eventMessages]
  const completedSlots = new Set(authoritative.map(historySlot))
  return [
    ...artifactMessages.filter(
      (message) => !completedSlots.has(historySlot(message)),
    ),
    ...authoritative,
  ]
}

export function chatMessagesReducer(
  state: ChatMessagesState,
  action: ChatMessagesAction,
): ChatMessagesState {
  if (action.type === 'reset') return initialChatMessagesState
  if (action.type === 'sse') return applyChatSseEvent(state, action.event)
  if (action.type === 'revealCompleted') {
    const characters = [...action.message.content]
    const visible = Math.max(
      0,
      Math.min(action.visibleCodePoints, characters.length),
    )
    const revealing = visible < characters.length
    return withMessage(state, {
      ...action.message,
      content: characters.slice(0, visible).join(''),
      status: revealing ? 'streaming' : action.message.status,
      nextOffset: visible,
      revealing,
    })
  }
  if (action.type === 'interruptStreaming') {
    return stateFromMessages(Object.values(state.byId).map((message) =>
      message.status === 'streaming'
        ? { ...message, status: 'failed' as const, revealing: false }
        : message,
    ))
  }

  const eventMessages = messagesFromEvents(action.events ?? [])
  const suppliedMessages = action.messages ?? []
  const context = action.context ?? {
    runId: action.artifacts?.[0]?.run_id ?? 'legacy',
    attempt: action.artifacts?.[0]?.attempt ?? 1,
  }
  const artifactMessages = action.artifacts?.length
    ? messagesFromArtifacts(action.artifacts, context)
    : []
  const historicalMessages = mergeHistoryMessages(
    eventMessages,
    suppliedMessages,
    artifactMessages,
  )
  if (action.type === 'merge') {
    return mergeMessages(state, historicalMessages)
  }

  return historicalMessages.length
    ? stateFromMessages(historicalMessages)
    : initialChatMessagesState
}

export function orderedChatMessages(
  state: ChatMessagesState,
): CommitteeChatMessage[] {
  return state.order
    .map((messageId) => state.byId[messageId])
    .filter((message): message is CommitteeChatMessage => Boolean(message))
}
