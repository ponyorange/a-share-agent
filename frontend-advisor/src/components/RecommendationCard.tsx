import { Link } from 'react-router-dom'
import type { AdviceItem } from '../api'
import { formatPct, formatScore } from '../api'
import { explorerKlineUrl } from '../explorerLinks'
import { ActionBadge } from './AdviceCard'
import { StarToggle } from './StarToggle'

function chgClass(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return ''
  if (v > 0) return 'up'
  if (v < 0) return 'down'
  return ''
}

export function RecommendationCard({
  item,
  starred,
  starBusy,
  onToggleStar,
}: {
  item: AdviceItem
  starred?: boolean
  starBusy?: boolean
  onToggleStar?: (next: boolean) => void | Promise<void>
}) {
  return (
    <article className="recommendation-card">
      <header className="recommendation-card-head">
        <div>
          <h2>{item.name}</h2>
          <p className="mono">{item.symbol}</p>
        </div>
        <div className="recommendation-card-badges">
          <span className="score-pill">
            <span>评分</span>
            <strong>{formatScore(item.score)}</strong>
          </span>
          <ActionBadge action={item.action} label={item.action_label} />
        </div>
      </header>

      <dl className="recommendation-card-metrics">
        <div>
          <dt>收盘</dt>
          <dd>{item.close != null ? item.close : '—'}</dd>
        </div>
        <div>
          <dt>日涨幅</dt>
          <dd className={chgClass(item.day_chg_pct)}>
            {item.day_chg_pct == null ? '—' : formatPct(item.day_chg_pct, 2)}
          </dd>
        </div>
        <div>
          <dt>命中率</dt>
          <dd>{formatPct(item.hit_rate)}</dd>
        </div>
      </dl>

      <footer className="recommendation-card-actions">
        {onToggleStar ? (
          <StarToggle
            symbol={item.symbol}
            starred={Boolean(starred)}
            busy={starBusy}
            onToggle={onToggleStar}
          />
        ) : null}
        <Link className="text-link" to={`/advice?symbol=${item.symbol}`}>
          诊断
        </Link>
        <a
          className="text-link"
          href={explorerKlineUrl(item.symbol)}
          target="_blank"
          rel="noreferrer"
        >
          查看 K 线
        </a>
      </footer>
    </article>
  )
}
