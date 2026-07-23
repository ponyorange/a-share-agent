type Category = {
  id: string
  label: string
  count: number
}

type Props = {
  categories: Category[]
  active: string | null
  onSelect: (id: string | null) => void
  total: number
}

export function CategoryNav({ categories, active, onSelect, total }: Props) {
  return (
    <nav className="category-nav" aria-label="数据分类">
      <button
        type="button"
        className={`cat-item ${active === null ? 'active' : ''}`}
        onClick={() => onSelect(null)}
      >
        <span>全部</span>
        <span className="cat-count">{total}</span>
      </button>
      {categories.map((c) => (
        <button
          key={c.id}
          type="button"
          className={`cat-item ${active === c.id ? 'active' : ''}`}
          onClick={() => onSelect(c.id)}
        >
          <span>{c.label}</span>
          <span className="cat-count">{c.count}</span>
        </button>
      ))}
    </nav>
  )
}
