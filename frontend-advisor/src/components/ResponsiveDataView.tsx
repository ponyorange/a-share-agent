import { useEffect, useState, type ReactNode } from 'react'

export type DataViewMode = 'card' | 'table'

function readStoredMode(storageKey: string): DataViewMode {
  try {
    return localStorage.getItem(storageKey) === 'table' ? 'table' : 'card'
  } catch {
    return 'card'
  }
}

export function useMediaQuery(query: string) {
  const [matches, setMatches] = useState(() =>
    typeof window !== 'undefined' && typeof window.matchMedia === 'function'
      ? window.matchMedia(query).matches
      : false,
  )
  useEffect(() => {
    if (typeof window.matchMedia !== 'function') {
      setMatches(false)
      return
    }
    const media = window.matchMedia(query)
    const update = () => setMatches(media.matches)
    update()
    media.addEventListener('change', update)
    return () => media.removeEventListener('change', update)
  }, [query])
  return matches
}

export function ResponsiveDataView(props: {
  storageKey: string
  label: string
  cards: ReactNode
  table: ReactNode
}) {
  const isMobile = useMediaQuery('(max-width: 768px)')
  const [mode, setMode] = useState<DataViewMode>(() => readStoredMode(props.storageKey))
  useEffect(() => {
    setMode(readStoredMode(props.storageKey))
  }, [props.storageKey])
  const select = (next: DataViewMode) => {
    setMode(next)
    try {
      localStorage.setItem(props.storageKey, next)
    } catch {
      // 展示不依赖持久化成功
    }
  }
  if (!isMobile) return <>{props.table}</>
  return (
    <section className={`responsive-data-view responsive-data-view--${mode}`}>
      <div className="responsive-view-toggle" role="group" aria-label={`${props.label}视图`}>
        <button type="button" aria-pressed={mode === 'card'} onClick={() => select('card')}>
          卡片视图
        </button>
        <button type="button" aria-pressed={mode === 'table'} onClick={() => select('table')}>
          表格视图
        </button>
      </div>
      {mode === 'card' ? props.cards : props.table}
    </section>
  )
}
