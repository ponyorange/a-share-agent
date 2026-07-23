import { useCallback, useMemo, useState } from 'react'
import type {
  CommitteeArtifact,
  CommitteeEventRecord,
  CommitteeRun,
} from '../committeeApi'
import CommitteeDialog from './CommitteeDialog'
import EvidenceBadge from './EvidenceBadge'

type JsonRecord = Record<string, unknown>

const NODES = [
  ['prepare', '数据快照'],
  ['fundamental', '基本面分析'],
  ['technical', '技术面分析'],
  ['news', '新闻分析'],
  ['quant', '量化分析'],
  ['bull', '多方辩论'],
  ['bear', '空方辩论'],
  ['trader', '交易提案'],
  ['backtest', '组合回测'],
  ['risk', '风险审核'],
  ['chair', '主席决策'],
] as const

const STATUS_TEXT: Record<string, string> = {
  running: '运行中',
  completed: '已完成',
  degraded: '降级',
  aborted: '中止',
  error: '错误',
  needs_revision: '需修订',
}

function records(value: unknown): JsonRecord[] {
  if (Array.isArray(value)) return value.filter((item): item is JsonRecord => !!item && typeof item === 'object')
  return value && typeof value === 'object' ? [value as JsonRecord] : []
}

function artifactPayloads(artifacts: CommitteeArtifact[], kind: string) {
  return artifacts
    .filter((artifact) => artifact.kind === kind)
    .flatMap((artifact) => records(artifact.payload))
}

function nodeState(node: string, events: CommitteeEventRecord[]) {
  const relevant = events.filter((event) => {
    const eventNode = String(event.payload.node ?? event.node ?? '')
    return eventNode === node || eventNode.startsWith(`${node}:`)
  })
  const event = relevant.at(-1)
  if (!event) return 'pending'
  if (['degraded', 'aborted', 'error', 'needs_revision'].includes(event.event_type)) {
    return event.event_type
  }
  return event.event_type === 'completed' || event.event_type === 'node_completed'
    ? 'completed'
    : 'running'
}

function confidence(value: unknown) {
  return typeof value === 'number' ? `${(value * 100).toFixed(0)}%` : '—'
}

function EvidenceDrawer({
  evidence,
  onClose,
}: {
  evidence: JsonRecord[]
  onClose: () => void
}) {
  return (
    <CommitteeDialog title="证据详情" onClose={onClose}>
      <div className="committee-evidence-list">
        {evidence.map((item, index) => (
          <article key={String(item.evidence_id ?? index)}>
            <div className="committee-card-head">
              <strong>{String(item.source ?? '未知来源')}</strong>
              <EvidenceBadge kind={item.degraded ? 'degraded' : 'fact'} />
            </div>
            <dl>
              <div><dt>evidence id</dt><dd className="mono">{String(item.evidence_id ?? '—')}</dd></div>
              <div><dt>captured_at</dt><dd>{String(item.captured_at ?? '—')}</dd></div>
              <div><dt>data_as_of</dt><dd>{String(item.data_as_of ?? '—')}</dd></div>
              <div><dt>freshness</dt><dd>{String(item.freshness ?? '—')}</dd></div>
            </dl>
          </article>
        ))}
        {!evidence.length ? <p className="status">暂无证据引用</p> : null}
      </div>
    </CommitteeDialog>
  )
}

export default function CommitteeDetail({
  run,
  artifacts,
  events,
  streamState,
}: {
  run: CommitteeRun
  artifacts: CommitteeArtifact[]
  events: CommitteeEventRecord[]
  streamState: string
}) {
  const [showEvidence, setShowEvidence] = useState(false)
  const closeEvidence = useCallback(() => setShowEvidence(false), [])
  const reports = artifactPayloads(artifacts, 'analyst_reports')
  const debates = artifactPayloads(artifacts, 'debate_turns').sort(
    (left, right) => Number(left.sequence ?? 0) - Number(right.sequence ?? 0),
  )
  const backtest = artifactPayloads(artifacts, 'backtest_verdict').at(-1)
  const risk = artifactPayloads(artifacts, 'risk_verdict').at(-1)
  const decision = artifactPayloads(artifacts, 'final_decision').at(-1)
  const snapshots = artifactPayloads(artifacts, 'snapshot')
  const budget = artifactPayloads(artifacts, 'budget').at(-1)
  const calls = artifactPayloads(artifacts, 'model_calls')
  const errors = artifactPayloads(artifacts, 'errors')
  const evidence = useMemo(() => {
    const all = [...reports, ...debates, ...(decision ? [decision] : [])]
    return all.flatMap((item) => [
      ...records(item.evidence),
      ...records(item.evidence_refs),
    ])
  }, [reports, debates, decision])
  const duration =
    run.started_at && run.completed_at
      ? (new Date(run.completed_at).getTime() - new Date(run.started_at).getTime()) / 1000
      : null
  const modelVersions = [
    ...new Set(
      calls
        .map((call) => String(call.model ?? call.model_name ?? ''))
        .filter(Boolean),
    ),
  ]
  const riskReasons = Array.isArray(risk?.reasons) ? risk.reasons : []
  const orderKeys = new Set(
    records(decision?.orders).map(
      (order) => `${String(order.symbol)}:${String(order.direction)}`,
    ),
  )
  const vetoed = events.some(
    (event) => event.event_type === 'completed' && event.payload.vetoed === true,
  )

  return (
    <div className="committee-detail committee-detail--drawer">
      <section className="committee-overview">
        <div>
          <span className={`committee-status committee-status--${run.status}`}>{run.status}</span>
          <h2 className="mono">{run.run_id}</h2>
          <p>{run.universe.join(' · ')}</p>
        </div>
        <div className="committee-version-grid">
          <span>数据版本 <b>{run.snapshot_id?.slice(0, 12) ?? '待生成'}</b></span>
          <span>模型版本 <b>{modelVersions.join(', ') || (calls.length ? `${calls.length} 次调用` : '—')}</b></span>
          <span>策略版本 <b>{run.strategy_version}</b></span>
          <span>耗时 <b>{duration == null ? '—' : `${duration.toFixed(1)}s`}</b></span>
          <span>Token <b>{String(budget?.tokens ?? '—')}</b></span>
          <span>流状态 <b>{streamState}</b></span>
        </div>
      </section>

      {run.error_message || errors.length ? (
        <section className="committee-alert" role="alert">
          <strong>失败原因</strong>
          <p>{run.error_message ?? errors.map((item) => String(item.message ?? '')).join('；')}</p>
        </section>
      ) : null}

      <section className="committee-panel">
        <div className="committee-section-head">
          <h2>实时时间线</h2>
          <span className="muted">Mongo 历史事件 + SSE 续传</span>
        </div>
        <ol className="committee-timeline">
          {NODES.map(([node, label]) => {
            const status = nodeState(node, events)
            return (
              <li key={node} data-status={status}>
                <span className="committee-timeline-dot" />
                <strong>{label}</strong>
                <span>{STATUS_TEXT[status] ?? '等待'}</span>
                {status === 'degraded' ? <EvidenceBadge kind="degraded" /> : null}
              </li>
            )
          })}
        </ol>
      </section>

      <section className="committee-panel">
        <div className="committee-section-head">
          <h2>四方分析报告</h2>
          <button type="button" className="btn ghost" onClick={() => setShowEvidence(true)}>
            查看证据
          </button>
        </div>
        <div className="committee-report-grid">
          {(['fundamental', 'technical', 'news', 'quant'] as const).map((role) => {
            const report = reports.filter((item) => item.role === role).at(-1)
            const label = NODES.find(([node]) => node === role)?.[1]
            return (
              <article className="committee-card" key={role}>
                <div className="committee-card-head">
                  <h3>{label}</h3>
                  <EvidenceBadge kind="judgement" />
                </div>
                {report ? (
                  <>
                    <p>{String(report.thesis ?? '无结论')}</p>
                    <small>置信度 {confidence(report.confidence)}</small>
                  </>
                ) : <p className="muted">等待报告</p>}
              </article>
            )
          })}
        </div>
      </section>

      <section className="committee-panel">
        <h2>多空辩论</h2>
        <div className="committee-debate">
          {debates.map((turn, index) => (
            <article key={`${String(turn.sequence)}:${String(turn.speaker)}:${index}`}>
              <span>第 {String(turn.sequence ?? index + 1)} 轮</span>
              <strong>{turn.speaker === 'bull' ? '多方' : '空方'}</strong>
              <p>{String(turn.argument ?? '')}</p>
              <small>置信度 {confidence(turn.confidence)}</small>
            </article>
          ))}
          {!debates.length ? <p className="status">暂无辩论记录</p> : null}
        </div>
      </section>

      <div className="committee-result-grid">
        <section className="committee-panel">
          <div className="committee-card-head">
            <h2>回测指标</h2>
            <EvidenceBadge kind="fact" />
          </div>
          {backtest ? (
            <>
              <p>{String(backtest.summary ?? '')}</p>
              <p>评分 {confidence(backtest.score)} · {backtest.passed ? '通过' : '未通过'}</p>
              <dl className="committee-metrics">
                {Object.entries((backtest.metrics as JsonRecord) ?? {}).map(([key, value]) => (
                  <div key={key}><dt>{key}</dt><dd>{String(value)}</dd></div>
                ))}
              </dl>
            </>
          ) : <p className="status">等待回测</p>}
        </section>
        <section className="committee-panel">
          <div className="committee-card-head">
            <h2>风控规则</h2>
            <EvidenceBadge kind="hard_rule" />
          </div>
          {records(risk?.rules).map((rule) => (
            <article className="committee-rule" key={String(rule.rule_id)}>
              <strong>{String(rule.rule_id)}</strong>
              <span className={`committee-status committee-status--${String(rule.severity)}`}>
                {String(rule.severity)}
              </span>
              <p>observed {String(rule.observed)} / limit {String(rule.limit)}</p>
              <small>{String(rule.message)}</small>
            </article>
          ))}
          {!risk ? <p className="status">等待风控</p> : null}
        </section>
      </div>

      <section className="committee-panel">
        <div className="committee-card-head">
          <h2>最终组合决策</h2>
          <div className="field-actions">
            {vetoed ? <span className="committee-status committee-status--hard">主席否决</span> : null}
            <span className={`committee-status committee-status--${String(decision?.risk_status ?? 'pending')}`}>
              {String(decision?.risk_status ?? '等待主席')}
            </span>
          </div>
        </div>
        {decision ? (
          <>
            <p>{String(decision.rationale ?? '')}</p>
            <p>主决策：{String(decision.action)} {String(decision.symbol)} · 目标权重 {confidence(decision.target_weight)}</p>
            <h3>Proposals / Orders</h3>
            <div className="table-wrap">
              <table className="data-table">
                <thead><tr><th>证券</th><th>方向</th><th>目标权重</th><th>置信度</th><th>风险/反对意见</th></tr></thead>
                <tbody>
                  {records(decision.proposals).map((proposal, index) => (
                    <tr key={`${String(proposal.symbol)}:${index}`}>
                      <td className="mono">{String(proposal.symbol)}</td>
                      <td>{String(proposal.direction)}</td>
                      <td>{confidence(proposal.target_weight)}</td>
                      <td>{confidence(proposal.confidence)}</td>
                      <td>
                        {orderKeys.has(`${String(proposal.symbol)}:${String(proposal.direction)}`)
                          ? '已锁定订单 · '
                          : '仅提案 · '}
                        {String(proposal.rationale ?? '')}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {riskReasons.length ? (
              <ul>{riskReasons.map((reason, index) => <li key={index}>{String(reason)}</li>)}</ul>
            ) : null}
          </>
        ) : <p className="status">等待主席决策</p>}
      </section>
      {snapshots.length ? <p className="muted">快照工件已冻结，可用于历史重放。</p> : null}
      {showEvidence ? <EvidenceDrawer evidence={evidence} onClose={closeEvidence} /> : null}
    </div>
  )
}
