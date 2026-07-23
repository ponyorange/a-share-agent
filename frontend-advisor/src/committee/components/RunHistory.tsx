import { useMemo, useState } from 'react'
import type { CommitteeRun } from '../committeeApi'

const STATUS_LABELS: Record<string, string> = {
  queued: '排队',
  running: '进行中',
  completed: '完成',
  failed: '失败',
  cancelled: '已取消',
}

export default function RunHistory({
  runs,
  selectedId,
  loading,
  selectionLocked = false,
  onSelect,
}: {
  runs: CommitteeRun[]
  selectedId?: string
  loading: boolean
  selectionLocked?: boolean
  onSelect: (runId: string) => void
}) {
  const [status, setStatus] = useState('all')
  const filtered = useMemo(
    () => runs.filter((run) => status === 'all' || run.status === status),
    [runs, status],
  )
  return (
    <aside className="committee-history" aria-label="历史会议">
      <div className="committee-section-head">
        <h2>历史会议</h2>
        <select
          className="input"
          aria-label="筛选会议状态"
          value={status}
          onChange={(event) => setStatus(event.target.value)}
        >
          <option value="all">全部状态</option>
          <option value="running">进行中</option>
          <option value="completed">已完成</option>
          <option value="failed">失败</option>
          <option value="cancelled">已取消</option>
        </select>
      </div>
      {loading ? <p className="status">加载会议…</p> : null}
      {!loading && !filtered.length ? <p className="status">暂无会议</p> : null}
      <div className="committee-run-list">
        {filtered.map((run) => (
          <button
            type="button"
            key={run.run_id}
            className={`committee-run${selectedId === run.run_id ? ' active' : ''}`}
            aria-pressed={selectedId === run.run_id}
            disabled={selectionLocked}
            onClick={() => {
              if (!selectionLocked) onSelect(run.run_id)
            }}
          >
            <span className="committee-run-title mono">{run.run_id.slice(0, 10)}</span>
            <span className={`committee-status committee-status--${run.status}`}>
              {STATUS_LABELS[run.status] ?? run.status}
            </span>
            <small>attempt {run.attempt}</small>
            {run.parent_run_id ? (
              <small className="mono">parent {run.parent_run_id.slice(0, 8)}</small>
            ) : null}
            <time>{new Date(run.created_at).toLocaleString('zh-CN')}</time>
          </button>
        ))}
      </div>
    </aside>
  )
}
