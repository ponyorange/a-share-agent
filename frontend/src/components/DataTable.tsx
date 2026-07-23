import type { FetchResult } from '../api'

type Props = {
  result: FetchResult | null
  view: 'table' | 'json'
}

function cellText(value: unknown): string {
  if (value === null || value === undefined) return ''
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

export function DataTable({ result, view }: Props) {
  if (!result) {
    return <div className="table-empty">选择接口并拉取数据后，结果将显示在这里。</div>
  }

  if (view === 'json') {
    return (
      <pre className="json-view">{JSON.stringify(result, null, 2)}</pre>
    )
  }

  if (!result.rows.length) {
    return <div className="table-empty">返回为空（0 行）。</div>
  }

  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            {result.columns.map((col) => (
              <th key={col}>{col}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {result.rows.map((row, idx) => (
            <tr key={idx}>
              {result.columns.map((col) => (
                <td key={col}>{cellText(row[col])}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
