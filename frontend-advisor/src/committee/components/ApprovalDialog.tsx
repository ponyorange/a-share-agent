import { useCallback, useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  approveCommitteeRun,
  bindCommitteeOrderPreview,
  getCommitteeOrderPreview,
  type ApprovalPreview,
  type CommitteeRun,
} from '../committeeApi'
import CommitteeDialog from './CommitteeDialog'

type BoundPreview = {
  preview_id: string
  preview: ApprovalPreview
}

type PendingApproval = {
  run_id: string
  preview_id: string
  decision_hash: string
  proposal_hash: string
  account_version: number
  preview: ApprovalPreview
  idempotency_key: string
  outcome_unknown: boolean
}

type ApprovalErrorKind =
  | 'none'
  | 'unknown'
  | 'invalid'
  | 'auth'
  | 'deterministic'

function errorStatus(cause: unknown) {
  return typeof cause === 'object' && cause !== null && 'status' in cause
    ? Number(cause.status)
    : undefined
}

function errorMessage(cause: unknown) {
  if (cause instanceof Error) return cause.message
  if (typeof cause === 'object' && cause !== null && 'message' in cause) {
    return String(cause.message)
  }
  return String(cause)
}

function isExplicitlyInvalid(cause: unknown) {
  const status = errorStatus(cause)
  return (
    status != null &&
    [409, 412].includes(status) &&
    /preview|预览|version|版本|account|账户|hash|哈希|quote|报价|偏离|expiry|expires|过期|risk|风险|状态已变化/i.test(
      errorMessage(cause),
    )
  )
}

function classifyApprovalError(cause: unknown): ApprovalErrorKind {
  const status = errorStatus(cause)
  const message = errorMessage(cause)
  if (status === 401) return 'auth'
  if (isExplicitlyInvalid(cause)) return 'invalid'
  if (
    status === 409 &&
    /正在执行|处理中|同一.*幂等|同一.*key|in progress|same idempotency/i.test(message)
  ) {
    return 'unknown'
  }
  if (status === 408 || (status != null && status >= 500)) return 'unknown'
  if (status != null && status >= 400 && status < 500) return 'deterministic'
  if (
    (cause instanceof DOMException && cause.name === 'AbortError') ||
    cause instanceof TypeError ||
    /network|failed to fetch|load failed|timeout|timed out|网络|超时/i.test(message)
  ) {
    return 'unknown'
  }
  return 'deterministic'
}

function pendingStorageKey(runId: string) {
  return `committee:pending-approval:${runId}`
}

function readPendingApproval(runId: string): PendingApproval | null {
  try {
    const raw = sessionStorage.getItem(pendingStorageKey(runId))
    if (!raw) return null
    const pending = JSON.parse(raw) as PendingApproval
    if (
      pending.run_id !== runId ||
      !pending.preview_id ||
      !pending.idempotency_key ||
      pending.decision_hash !== pending.preview?.decision_hash ||
      pending.proposal_hash !== pending.preview?.proposal_hash ||
      pending.account_version !== pending.preview?.account_version
    ) {
      return null
    }
    return pending
  } catch {
    return null
  }
}

function savePendingApproval(pending: PendingApproval) {
  try {
    sessionStorage.setItem(
      pendingStorageKey(pending.run_id),
      JSON.stringify(pending),
    )
  } catch {
    // 存储不可用时仍保留当前内存中的稳定审批尝试
  }
}

function clearPendingApproval(runId: string) {
  try {
    sessionStorage.removeItem(pendingStorageKey(runId))
  } catch {
    // ignore unavailable session storage
  }
}

export default function ApprovalDialog({
  run,
  onClose,
}: {
  run: CommitteeRun
  onClose: () => void
}) {
  const [restoredPending] = useState(() => readPendingApproval(run.run_id))
  const [preview, setPreview] = useState<ApprovalPreview | null>(
    restoredPending?.preview ?? null,
  )
  const [boundPreview, setBoundPreview] = useState<BoundPreview | null>(
    restoredPending
      ? {
          preview_id: restoredPending.preview_id,
          preview: restoredPending.preview,
        }
      : null,
  )
  const [approvalKey, setApprovalKey] = useState<string | null>(
    restoredPending?.idempotency_key ?? null,
  )
  const [stage, setStage] = useState<'preview' | 'confirm' | 'success'>(
    restoredPending ? 'confirm' : 'preview',
  )
  const [loading, setLoading] = useState(!restoredPending)
  const [submitting, setSubmitting] = useState(false)
  const [outcomeUnknown, setOutcomeUnknown] = useState(
    restoredPending?.outcome_unknown ?? false,
  )
  const [errorKind, setErrorKind] = useState<ApprovalErrorKind>(
    restoredPending?.outcome_unknown ? 'unknown' : 'none',
  )
  const submittingRef = useRef(false)
  const requestAbort = useRef<AbortController | null>(null)
  const [error, setError] = useState(
    restoredPending?.outcome_unknown
      ? '上次提交状态未知/稍后用同一key重试'
      : '',
  )

  const invalidatePreview = useCallback(() => {
    clearPendingApproval(run.run_id)
    setPreview(null)
    setBoundPreview(null)
    setApprovalKey(null)
    setOutcomeUnknown(false)
    setErrorKind('none')
    setStage('preview')
  }, [run.run_id])

  const loadPreview = useCallback(async () => {
    requestAbort.current?.abort()
    const controller = new AbortController()
    requestAbort.current = controller
    setLoading(true)
    setError('')
    invalidatePreview()
    try {
      const result = await getCommitteeOrderPreview(run.run_id, controller.signal)
      if (!controller.signal.aborted) setPreview(result.preview)
    } catch (cause) {
      if (controller.signal.aborted) return
      setPreview(null)
      setError(cause instanceof Error ? cause.message : '预览失败')
    } finally {
      if (!controller.signal.aborted) setLoading(false)
    }
  }, [invalidatePreview, run.run_id])

  useEffect(() => {
    if (!restoredPending) void loadPreview()
    return () => requestAbort.current?.abort()
  }, [loadPreview, restoredPending])

  async function prepareConfirmation() {
    if (!preview || submittingRef.current) return
    submittingRef.current = true
    setSubmitting(true)
    setError('')
    requestAbort.current?.abort()
    const controller = new AbortController()
    requestAbort.current = controller
    try {
      const bound = await bindCommitteeOrderPreview(
        run.run_id,
        preview,
        controller.signal,
      )
      if (controller.signal.aborted) return
      const key = [
        'committee-approve',
        crypto.randomUUID(),
        run.run_id,
        String(run.version),
        bound.preview_id,
      ].join(':')
      setBoundPreview(bound)
      setApprovalKey(key)
      savePendingApproval({
        run_id: run.run_id,
        preview_id: bound.preview_id,
        decision_hash: bound.preview.decision_hash,
        proposal_hash: bound.preview.proposal_hash,
        account_version: bound.preview.account_version,
        preview: bound.preview,
        idempotency_key: key,
        outcome_unknown: false,
      })
      setStage('confirm')
    } catch (cause) {
      if (controller.signal.aborted) return
      invalidatePreview()
      setError(
        `${cause instanceof Error ? cause.message : '预览绑定失败'}；请重新预览`,
      )
    } finally {
      submittingRef.current = false
      if (!controller.signal.aborted) setSubmitting(false)
    }
  }

  async function confirm() {
    if (!boundPreview || !approvalKey || submittingRef.current) return
    submittingRef.current = true
    setSubmitting(true)
    setError('')
    requestAbort.current?.abort()
    const controller = new AbortController()
    requestAbort.current = controller
    setOutcomeUnknown(true)
    setErrorKind('unknown')
    savePendingApproval({
      run_id: run.run_id,
      preview_id: boundPreview.preview_id,
      decision_hash: boundPreview.preview.decision_hash,
      proposal_hash: boundPreview.preview.proposal_hash,
      account_version: boundPreview.preview.account_version,
      preview: boundPreview.preview,
      idempotency_key: approvalKey,
      outcome_unknown: true,
    })
    try {
      await approveCommitteeRun(
        run.run_id,
        {
          preview_id: boundPreview.preview_id,
          decision_hash: boundPreview.preview.decision_hash,
          proposal_hash: boundPreview.preview.proposal_hash,
          account_version: boundPreview.preview.account_version,
          confirm: true,
        },
        approvalKey,
        controller.signal,
      )
      if (controller.signal.aborted) return
      clearPendingApproval(run.run_id)
      setOutcomeUnknown(false)
      setErrorKind('none')
      setStage('success')
    } catch (cause) {
      if (controller.signal.aborted) return
      const kind = classifyApprovalError(cause)
      if (kind === 'invalid') {
        invalidatePreview()
        setErrorKind('invalid')
        setError(
          `${errorMessage(cause)}；请重新预览后确认`,
        )
      } else if (kind === 'auth') {
        invalidatePreview()
        setErrorKind('auth')
        setError('登录已失效，审批已停止')
      } else if (kind === 'unknown') {
        setOutcomeUnknown(true)
        setErrorKind('unknown')
        setError('提交状态未知/稍后用同一key重试')
      } else {
        setOutcomeUnknown(false)
        setErrorKind('deterministic')
        savePendingApproval({
          run_id: run.run_id,
          preview_id: boundPreview.preview_id,
          decision_hash: boundPreview.preview.decision_hash,
          proposal_hash: boundPreview.preview.proposal_hash,
          account_version: boundPreview.preview.account_version,
          preview: boundPreview.preview,
          idempotency_key: approvalKey,
          outcome_unknown: false,
        })
        const status = errorStatus(cause)
        setError(
          `提交失败${status ? `（HTTP ${status}）` : ''}：${errorMessage(cause)}；已保留审批审计信息`,
        )
      }
    } finally {
      submittingRef.current = false
      if (!controller.signal.aborted) setSubmitting(false)
    }
  }

  return (
    <CommitteeDialog
      title="审批订单"
      onClose={onClose}
      closeDisabled={submitting || outcomeUnknown}
    >
      {loading ? <p className="status">正在校验报价、账户与风险…</p> : null}
      {error ? (
        <div className="committee-alert" role="alert">
          <p>{error}</p>
          {errorKind === 'invalid' ? (
            <button type="button" className="btn ghost" onClick={() => void loadPreview()}>
              重新预览
            </button>
          ) : null}
        </div>
      ) : null}
      {preview && stage !== 'success' ? (
        <>
          <div className="committee-preview-meta">
            <strong>账户版本 {preview.account_version}</strong>
            <span>决策版本 {run.version}</span>
            <span>价格偏离：后端硬规则阈值内</span>
            <span>有效期与风险：提交前再次校验</span>
          </div>
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>证券</th>
                  <th>方向</th>
                  <th>数量</th>
                  <th>当前报价</th>
                </tr>
              </thead>
              <tbody>
                {preview.orders.map((order) => (
                  <tr key={`${order.symbol}:${order.side}`}>
                    <td className="mono">{order.symbol}</td>
                    <td>{order.side}</td>
                    <td>{order.qty}</td>
                    <td>{order.price}</td>
                  </tr>
                ))}
                {!preview.orders.length ? (
                  <tr>
                    <td colSpan={4}>无可执行订单</td>
                  </tr>
                ) : null}
              </tbody>
            </table>
          </div>
          <p className="muted mono">决策哈希 {preview.decision_hash.slice(0, 16)}…</p>
          {stage === 'preview' ? (
            <button
              type="button"
              className="btn"
              disabled={!preview.orders.length}
              onClick={() => void prepareConfirmation()}
            >
              {submitting ? '绑定预览中…' : '进入二次确认'}
            </button>
          ) : (
            <div className="committee-confirm">
              <p>确认按以上稳定版本提交至模拟盘？提交期间不可重复操作。</p>
              <button
                type="button"
                className="btn"
                disabled={submitting}
                onClick={() => void confirm()}
              >
                {submitting
                  ? '提交中…'
                  : outcomeUnknown
                    ? '用同一key重试'
                    : '确认提交模拟盘'}
              </button>
            </div>
          )}
        </>
      ) : null}
      {stage === 'success' ? (
        <div className="committee-success" role="status">
          <p>订单已成功提交模拟盘。</p>
          <Link className="btn" to="/paper">
            前往模拟盘
          </Link>
        </div>
      ) : null}
    </CommitteeDialog>
  )
}
