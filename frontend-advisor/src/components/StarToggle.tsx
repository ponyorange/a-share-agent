type StarToggleProps = {
  symbol: string
  starred: boolean
  busy?: boolean
  onToggle: (next: boolean) => void | Promise<void>
}

export function StarToggle({ symbol, starred, busy, onToggle }: StarToggleProps) {
  return (
    <button
      type="button"
      className={`star-toggle${starred ? ' is-starred' : ''}`}
      aria-pressed={starred}
      aria-label={starred ? `取消收藏 ${symbol}` : `收藏 ${symbol}`}
      title={starred ? '取消收藏' : '收藏'}
      disabled={busy}
      onClick={() => void onToggle(!starred)}
    >
      <span aria-hidden="true">{starred ? '★' : '☆'}</span>
    </button>
  )
}
