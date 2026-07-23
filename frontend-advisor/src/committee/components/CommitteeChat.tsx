import { useMemo, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import { Virtuoso, type VirtuosoHandle } from 'react-virtuoso'
import remarkGfm from 'remark-gfm'
import type { CommitteeArtifact } from '../committeeApi'
import {
  ROLE_META,
  type CommitteeChatMessage,
} from '../chatMessages'

type JsonRecord = Record<string, unknown>

const STATUS_LABEL = {
  streaming: '输出中',
  completed: '已完成',
  degraded: '降级完成',
  failed: '失败',
} as const

const SENSITIVE_FIELD =
  /(key|token|secret|password|prompt|authorization|stack|trace|raw)/i
const MAX_CARD_TEXT = 200

const CARD_FIELDS: Record<string, Array<[key: string, label: string]>> = {
  snapshot: [
    ['as_of', '数据时点'],
    ['universe', '标的'],
    ['source', '来源'],
  ],
  analyst_reports: [
    ['role', '角色'],
    ['thesis', '结论'],
    ['confidence', '置信度'],
  ],
  debate_turns: [
    ['sequence', '轮次'],
    ['speaker', '辩方'],
    ['argument', '论点'],
    ['confidence', '置信度'],
  ],
  trade_proposal: [
    ['symbol', '证券'],
    ['direction', '方向'],
    ['target_weight', '目标权重'],
    ['confidence', '置信度'],
    ['rationale', '理由'],
  ],
  trade_proposals: [
    ['symbol', '证券'],
    ['direction', '方向'],
    ['target_weight', '目标权重'],
    ['confidence', '置信度'],
    ['rationale', '理由'],
  ],
  backtest_verdict: [
    ['passed', '是否通过'],
    ['score', '评分'],
    ['summary', '摘要'],
  ],
  risk_verdict: [
    ['status', '状态'],
    ['approved_weight', '批准权重'],
    ['reasons', '原因'],
  ],
  final_decision: [
    ['symbol', '证券'],
    ['action', '行动'],
    ['target_weight', '目标权重'],
    ['risk_status', '风险状态'],
    ['rationale', '理由'],
  ],
}

function record(value: unknown): JsonRecord | undefined {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as JsonRecord
    : undefined
}

function artifactRecords(artifacts: CommitteeArtifact[], kind: string) {
  return artifacts
    .filter((artifact) => artifact.kind === kind)
    .flatMap((artifact) => {
      if (Array.isArray(artifact.payload)) {
        return artifact.payload
          .map(record)
          .filter((item): item is JsonRecord => Boolean(item))
      }
      const item = record(artifact.payload)
      return item ? [item] : []
    })
}

function messageArtifact(
  message: CommitteeChatMessage,
  artifacts: CommitteeArtifact[],
) {
  const reference = message.card_ref
  const kind = reference?.kind ?? message.card_kind
  if (!kind) return undefined
  if (reference) {
    const exact = artifacts.find((artifact) =>
      artifact.kind === reference.kind
      && artifact.attempt === reference.attempt
      && artifact.node === reference.node,
    )
    if (exact) return exact
  }
  return artifacts.find((artifact) => artifact.kind === kind)
}

function truncateText(value: string) {
  const characters = [...value]
  return characters.length > MAX_CARD_TEXT
    ? `${characters.slice(0, MAX_CARD_TEXT).join('')}…`
    : value
}

function displayValue(value: unknown) {
  if (value == null) return '—'
  if (typeof value === 'boolean') return value ? '是' : '否'
  if (typeof value === 'number') return String(value)
  if (typeof value === 'string') return truncateText(value)
  if (Array.isArray(value)) {
    const values = value
      .filter((item) => ['string', 'number', 'boolean'].includes(typeof item))
      .slice(0, 3)
      .map(String)
    return values.length ? truncateText(values.join('；')) : `${value.length} 项`
  }
  return undefined
}

function summaryEntries(kind: string, payload: unknown) {
  const item = Array.isArray(payload) ? record(payload[0]) : record(payload)
  if (!item) return []
  return (CARD_FIELDS[kind] ?? []).flatMap(([key, label]) => {
    if (SENSITIVE_FIELD.test(key)) return []
    const value = displayValue(item[key])
    return value === undefined ? [] : [[label, value] as const]
  })
}

function formatTime(value?: string | null) {
  if (!value) return ''
  const date = new Date(value)
  return Number.isNaN(date.getTime())
    ? ''
    : date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
}

function DataCard({
  artifact,
}: {
  artifact: CommitteeArtifact
}) {
  const entries = summaryEntries(artifact.kind, artifact.payload)
  return (
    <details className="committee-card committee-chat-card">
      <summary>数据卡 · {artifact.kind}</summary>
      {entries.length ? (
        <dl>
          {entries.map(([key, value]) => (
            <div key={key}>
              <dt>{key}</dt>
              <dd>{value}</dd>
            </div>
          ))}
        </dl>
      ) : (
        <p className="muted">包含只读结构化数据</p>
      )}
    </details>
  )
}

function ChatBubble({
  message,
  artifacts,
}: {
  message: CommitteeChatMessage
  artifacts: CommitteeArtifact[]
}) {
  const meta = ROLE_META[message.role]
  const artifact = messageArtifact(message, artifacts)
  const content = message.content || (message.status === 'streaming' ? '…' : '')
  return (
    <article className={`committee-bubble committee-bubble--${meta.tone}`}>
      <span className="committee-avatar" aria-hidden="true">
        {meta.label.slice(0, 1)}
      </span>
      <div className="committee-bubble-body">
        <header>
          <strong>{meta.label}</strong>
          {message.round ? <span>第 {message.round} 轮</span> : null}
          <time dateTime={message.created_at ?? undefined}>
            {formatTime(message.created_at)}
          </time>
          <span className={`committee-bubble-status ${message.status}`}>
            {STATUS_LABEL[message.status]}
          </span>
        </header>
        <div className="committee-markdown">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
        </div>
        {artifact ? <DataCard artifact={artifact} /> : null}
      </div>
    </article>
  )
}

export default function CommitteeChat({
  messages,
  artifacts,
  loading,
  streamState,
}: {
  messages: CommitteeChatMessage[]
  artifacts: CommitteeArtifact[]
  loading: boolean
  streamState: string
}) {
  const virtuosoRef = useRef<VirtuosoHandle | null>(null)
  const [atBottom, setAtBottom] = useState(true)
  const roleSummary = useMemo(
    () => [...new Set(messages.map((message) => ROLE_META[message.role].label))],
    [messages],
  )
  const budget = artifactRecords(artifacts, 'budget').at(-1)
  const calls = artifactRecords(artifacts, 'model_calls')
  const durationMs = Number(budget?.duration_ms ?? budget?.elapsed_ms)
  const tokenCount = budget?.tokens
    ?? calls.reduce((total, call) => total + Number(call.tokens ?? call.total_tokens ?? 0), 0)
  const meetingStatus = loading
    ? '载入中'
    : messages.some((message) => message.status === 'streaming')
      ? '进行中'
      : messages.length
        ? '已同步'
        : '等待消息'

  return (
    <div className="committee-chat">
      <div className="committee-chat-summary" aria-label="群聊概览">
        <span>会议状态 <b>{meetingStatus}</b></span>
        <span>参与角色 <b>{roleSummary.join('、') || '—'}</b></span>
        {Number.isFinite(durationMs) ? <span>耗时 <b>{(durationMs / 1000).toFixed(1)}s</b></span> : null}
        {Number(tokenCount) > 0 ? <span>Token <b>{String(tokenCount)}</b></span> : null}
        <span>流状态 <b>{streamState}</b></span>
      </div>

      <div className="committee-chat-list">
        {messages.length ? (
          <Virtuoso
            ref={virtuosoRef}
            data={messages}
            computeItemKey={(_index, message) => message.message_id}
            increaseViewportBy={240}
            atBottomStateChange={setAtBottom}
            followOutput={(isAtBottom) => (isAtBottom ? 'smooth' : false)}
            itemContent={(_index, message) => (
              <ChatBubble message={message} artifacts={artifacts} />
            )}
          />
        ) : (
          <div className="committee-chat-empty">
            <p>{loading ? '载入会议消息…' : '暂无群聊消息'}</p>
            <small>此区域仅展示投委会输出，不提供输入框。</small>
          </div>
        )}
        {!atBottom && messages.length ? (
          <button
            type="button"
            className="btn committee-jump-latest"
            onClick={() => {
              virtuosoRef.current?.scrollToIndex({
                index: messages.length - 1,
                align: 'end',
                behavior: 'smooth',
              })
            }}
          >
            回到最新
          </button>
        ) : null}
      </div>
    </div>
  )
}
