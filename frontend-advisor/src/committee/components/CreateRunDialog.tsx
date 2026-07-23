import { useRef, useState } from 'react'
import { createCommitteeRun, type CommitteeRunCreate } from '../committeeApi'
import CommitteeDialog from './CommitteeDialog'

export default function CreateRunDialog({
  onClose,
  onCreated,
}: {
  onClose: () => void
  onCreated: (runId: string) => void
}) {
  const [symbolsText, setSymbolsText] = useState('')
  const [boards, setBoards] = useState<Array<'etf' | 'hs' | 'star'>>([])
  const [strategyVersion, setStrategyVersion] = useState('default')
  const [error, setError] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [idempotencyKey] = useState(
    () => `committee-create:${crypto.randomUUID()}`,
  )
  const submittingRef = useRef(false)

  function toggleBoard(board: 'etf' | 'hs' | 'star') {
    setBoards((current) =>
      current.includes(board)
        ? current.filter((item) => item !== board)
        : [...current, board],
    )
  }

  async function submit() {
    if (submittingRef.current) return
    const symbols = symbolsText
      .split(/[\s,，;；]+/)
      .map((item) => item.trim())
      .filter(Boolean)
    if (symbols.some((symbol) => !/^\d{6}$/.test(symbol))) {
      setError('请输入 6 位证券代码')
      return
    }
    if (!symbols.length && !boards.length) {
      setError('请选择候选池或输入证券代码')
      return
    }
    if (!strategyVersion.trim()) {
      setError('请输入策略版本')
      return
    }
    const body: CommitteeRunCreate = {
      symbols: [...new Set(symbols)],
      boards,
      horizon: 'next_day',
      strategy_version: strategyVersion.trim(),
    }
    submittingRef.current = true
    setSubmitting(true)
    setError('')
    try {
      const result = await createCommitteeRun(body, idempotencyKey)
      onCreated(result.run_id)
      onClose()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '发起会议失败')
    } finally {
      submittingRef.current = false
      setSubmitting(false)
    }
  }

  return (
    <CommitteeDialog title="发起投委会" onClose={onClose}>
      <div className="committee-form">
        <label>
          证券代码
          <input
            className="input mono"
            value={symbolsText}
            onChange={(event) => setSymbolsText(event.target.value)}
            placeholder="例如 510300，多个代码用逗号分隔"
          />
        </label>
        <fieldset>
          <legend>候选池（可与代码同时使用）</legend>
          {([
            ['hs', 'A股主板'],
            ['star', '科创板'],
            ['etf', 'ETF'],
          ] as const).map(([value, label]) => (
            <label key={value}>
              <input
                type="checkbox"
                checked={boards.includes(value)}
                onChange={() => toggleBoard(value)}
              />
              {label}
            </label>
          ))}
        </fieldset>
        <label>
          投资周期
          <input className="input" value="next_day" readOnly aria-label="投资周期" />
        </label>
        <label>
          策略版本
          <input
            className="input mono"
            value={strategyVersion}
            onChange={(event) => setStrategyVersion(event.target.value)}
          />
        </label>
      </div>
      {error ? <p className="status error" role="alert">{error}</p> : null}
      <button
        type="button"
        className="btn"
        disabled={submitting}
        onClick={() => void submit()}
      >
        {submitting ? '发起中…' : '确认发起'}
      </button>
    </CommitteeDialog>
  )
}
