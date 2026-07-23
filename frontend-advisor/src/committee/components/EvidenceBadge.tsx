export type EvidenceKind = 'fact' | 'judgement' | 'hard_rule' | 'degraded'

const LABELS: Record<EvidenceKind, string> = {
  fact: '事实',
  judgement: '模型判断',
  hard_rule: '硬规则',
  degraded: '降级',
}

export default function EvidenceBadge({ kind }: { kind: EvidenceKind }) {
  return (
    <span className={`committee-badge committee-badge--${kind}`} data-kind={kind}>
      {LABELS[kind]}
    </span>
  )
}
