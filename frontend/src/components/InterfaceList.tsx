import type { InterfaceSummary } from '../api'

type Props = {
  items: InterfaceSummary[]
  active: string | null
  onSelect: (name: string) => void
  loading?: boolean
}

export function InterfaceList({ items, active, onSelect, loading }: Props) {
  if (loading) {
    return <div className="list-empty">加载接口列表…</div>
  }
  if (!items.length) {
    return <div className="list-empty">没有匹配的接口</div>
  }
  return (
    <ul className="interface-list">
      {items.map((item) => (
        <li key={item.name}>
          <button
            type="button"
            className={`iface-item ${active === item.name ? 'active' : ''}`}
            onClick={() => onSelect(item.name)}
          >
            <code className="iface-name">{item.name}</code>
            {item.doc ? <span className="iface-doc">{item.doc}</span> : null}
          </button>
        </li>
      ))}
    </ul>
  )
}
