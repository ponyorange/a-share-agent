import type { ParamInfo } from '../api'

type Props = {
  params: ParamInfo[]
  values: Record<string, string>
  onChange: (name: string, value: string) => void
}

export function ParamForm({ params, values, onChange }: Props) {
  if (!params.length) {
    return (
      <p className="param-hint">此接口无需参数，可直接拉取。</p>
    )
  }

  return (
    <div className="param-form">
      {params.map((p) => (
        <label key={p.name} className="param-row">
          <span className="param-label">
            <code>{p.name}</code>
            {p.required ? <em className="req">必填</em> : <em className="opt">可选</em>}
            {p.annotation ? <span className="anno">{p.annotation}</span> : null}
          </span>
          <input
            type="text"
            value={values[p.name] ?? ''}
            placeholder={
              p.default !== null && p.default !== undefined
                ? `默认: ${String(p.default)}`
                : '输入参数值'
            }
            onChange={(e) => onChange(p.name, e.target.value)}
          />
        </label>
      ))}
    </div>
  )
}
