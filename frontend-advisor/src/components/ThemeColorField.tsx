import { normalizeHex, type ThemeColors } from '../theme/theme'

type ThemeColorFieldProps = {
  field: keyof ThemeColors
  label: string
  value: string
  error?: string | null
  warning?: string | null
  onChange: (field: keyof ThemeColors, value: string) => void
}

export default function ThemeColorField({
  field,
  label,
  value,
  error,
  warning,
  onChange,
}: ThemeColorFieldProps) {
  const colorValue = normalizeHex(value) ?? '#000000'

  return (
    <label className="theme-color-field">
      <span>{label}</span>
      <span className="theme-color-inputs">
        <input
          type="color"
          className="theme-swatch-input"
          aria-label={`${label}（颜色选择器）`}
          value={colorValue}
          onChange={(event) => onChange(field, event.target.value.toUpperCase())}
        />
        <input
          type="text"
          className="input mono theme-hex-input"
          aria-label={`${label}（十六进制）`}
          aria-invalid={error ? 'true' : undefined}
          value={value}
          onChange={(event) => onChange(field, event.target.value)}
        />
      </span>
      {error ? (
        <span className="status error theme-field-error" role="alert">
          {error}
        </span>
      ) : null}
      {!error && warning ? (
        <span className="theme-field-warning" role="status">
          {warning}
        </span>
      ) : null}
    </label>
  )
}
