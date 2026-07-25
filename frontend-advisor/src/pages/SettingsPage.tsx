import { useEffect, useMemo, useRef, useState, type CSSProperties } from 'react'
import ThemeColorField from '../components/ThemeColorField'
import { getContrastWarnings, normalizeHex, THEME_TEMPLATES, type ThemeColors, type ThemeId, type ThemeSettings } from '../theme/theme'
import { useTheme } from '../theme/ThemeProvider'

const COLOR_FIELDS: Array<{ field: keyof ThemeColors; label: string }> = [
  { field: 'page_bg', label: '页面背景' },
  { field: 'surface', label: '卡片背景' },
  { field: 'text_primary', label: '主文字' },
  { field: 'text_muted', label: '辅助文字' },
  { field: 'border', label: '边框' },
  { field: 'brand', label: '品牌主色' },
  { field: 'market_up', label: '上涨色' },
  { field: 'market_down', label: '下跌色' },
  { field: 'success', label: '成功色' },
  { field: 'error', label: '错误色' },
]

const TEMPLATE_LABELS: Record<ThemeId, string> = {
  modern_data: '现代数据',
  classic_market: '经典行情',
  deep_navy: '深海蓝',
}

type PreviewStyle = CSSProperties & Record<`--color-${string}`, string>

function cloneTheme(settings: ThemeSettings): ThemeSettings {
  return {
    ...settings,
    colors: { ...settings.colors },
  }
}

function normalizeColors(colors: ThemeColors): ThemeColors | null {
  const entries = COLOR_FIELDS.map(({ field }) => {
    const normalized = normalizeHex(colors[field])
    return normalized ? [field, normalized] : null
  })

  if (entries.some((entry) => entry === null)) {
    return null
  }

  return Object.fromEntries(entries as Array<[keyof ThemeColors, string]>) as ThemeColors
}

function fieldErrors(colors: ThemeColors): Partial<Record<keyof ThemeColors, string>> {
  return Object.fromEntries(
    COLOR_FIELDS.flatMap(({ field }) => (normalizeHex(colors[field]) ? [] : [[field, '请输入 #RRGGBB 格式']])),
  ) as Partial<Record<keyof ThemeColors, string>>
}

function previewStyle(colors: ThemeColors): PreviewStyle {
  return Object.fromEntries(
    COLOR_FIELDS.flatMap(({ field }) => {
      const normalized = normalizeHex(colors[field])
      return normalized ? [[`--color-${field.replaceAll('_', '-')}`, normalized]] : []
    }),
  ) as PreviewStyle
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : '主题保存失败'
}

export default function SettingsPage() {
  const { settings, loading, error: loadError, save } = useTheme()
  const lastSettingsRef = useRef(settings)
  const [draft, setDraft] = useState<ThemeSettings>(() => cloneTheme(settings))
  const [dirty, setDirty] = useState(false)
  const [saving, setSaving] = useState(false)
  const [message, setMessage] = useState<string | null>(null)
  const [saveError, setSaveError] = useState<string | null>(null)

  useEffect(() => {
    if (lastSettingsRef.current !== settings) {
      lastSettingsRef.current = settings
    } else {
      return
    }

    if (!dirty) {
      setDraft(cloneTheme(settings))
    }
  }, [dirty, settings])

  const errors = useMemo(() => fieldErrors(draft.colors), [draft.colors])
  const normalizedColors = useMemo(() => normalizeColors(draft.colors), [draft.colors])
  const contrastWarnings = useMemo(
    () => (normalizedColors ? getContrastWarnings(normalizedColors) : []),
    [normalizedColors],
  )
  const fieldWarnings = useMemo(() => {
    const byField: Partial<Record<keyof ThemeColors, string>> = {}
    for (const warning of contrastWarnings) {
      byField[warning.field] = `与 ${COLOR_FIELDS.find((item) => item.field === warning.against)?.label ?? warning.against} 对比度 ${warning.ratio.toFixed(2)}，建议 ≥ ${warning.minimum}`
    }
    return byField
  }, [contrastWarnings])
  const hasFieldErrors = Object.keys(errors).length > 0

  function updateColor(field: keyof ThemeColors, value: string) {
    setDraft((current) => ({ ...current, colors: { ...current.colors, [field]: value } }))
    setDirty(true)
    setMessage(null)
    setSaveError(null)
  }

  function switchTemplate(nextTemplate: ThemeId) {
    if (nextTemplate === draft.active_template) {
      return
    }

    if (dirty && !window.confirm('切换模板会覆盖当前未保存的配色，继续吗？')) {
      return
    }

    setDraft(cloneTheme(THEME_TEMPLATES[nextTemplate]))
    setDirty(true)
    setMessage(null)
    setSaveError(null)
  }

  function restoreTemplateDefaults() {
    setDraft(cloneTheme(THEME_TEMPLATES[draft.active_template]))
    setDirty(true)
    setMessage(null)
    setSaveError(null)
  }

  async function handleSave() {
    if (!normalizedColors) {
      return
    }

    const normalizedDraft: ThemeSettings = {
      ...draft,
      colors: normalizedColors,
    }

    setSaving(true)
    setMessage(null)
    setSaveError(null)
    try {
      const saved = await save(normalizedDraft)
      setDraft(cloneTheme(saved))
      setDirty(false)
      setMessage('配色已保存并应用')
    } catch (error) {
      setSaveError(errorMessage(error))
    } finally {
      setSaving(false)
    }
  }

  return (
    <section className="page theme-settings">
      <div className="page-hero">
        <p>编辑主题配色，先在右侧预览效果，确认后再保存并应用到全局界面。</p>
      </div>

      {loading ? <p className="status">主题加载中…</p> : null}
      {loadError ? <p className="status error">{loadError}</p> : null}
      {saveError ? (
        <p className="status error" role="alert">
          {saveError}
        </p>
      ) : null}
      {message ? (
        <p className="status ok" role="status">
          {message}
        </p>
      ) : null}

      <h2 className="section-title">主题模板</h2>
      <fieldset className="theme-template-grid" aria-label="主题模板">
        {Object.keys(THEME_TEMPLATES).map((id) => {
          const templateId = id as ThemeId
          const template = THEME_TEMPLATES[templateId]
          return (
            <label key={templateId} className="theme-template-card">
              <input
                type="radio"
                name="theme-template"
                aria-label={TEMPLATE_LABELS[templateId]}
                checked={draft.active_template === templateId}
                onChange={() => switchTemplate(templateId)}
              />
              <span>{TEMPLATE_LABELS[templateId]}</span>
              <span className="theme-template-swatches">
                <span
                  className="theme-template-swatch"
                  aria-label={`${TEMPLATE_LABELS[templateId]}品牌色`}
                  style={{ backgroundColor: template.colors.brand }}
                />
                <span
                  className="theme-template-swatch"
                  aria-label={`${TEMPLATE_LABELS[templateId]}上涨色`}
                  style={{ backgroundColor: template.colors.market_up }}
                />
                <span
                  className="theme-template-swatch"
                  aria-label={`${TEMPLATE_LABELS[templateId]}下跌色`}
                  style={{ backgroundColor: template.colors.market_down }}
                />
              </span>
              <small style={{ color: template.colors.text_muted }}>
                品牌色 {template.colors.brand}
              </small>
            </label>
          )
        })}
      </fieldset>

      <div className="theme-editor-layout">
        <div>
          <h2 className="section-title">颜色编辑</h2>
          <div className="theme-color-grid">
            {COLOR_FIELDS.map(({ field, label }) => (
              <ThemeColorField
                key={field}
                field={field}
                label={label}
                value={draft.colors[field]}
                error={errors[field]}
                warning={fieldWarnings[field]}
                onChange={updateColor}
              />
            ))}
          </div>
        </div>

        <aside className="theme-preview" data-testid="theme-preview" style={previewStyle(draft.colors)}>
          <h2 className="section-title">预览</h2>
          <div className="theme-preview-card">
            <p className="meta-line">组合收益</p>
            <strong className="up">+3.8% ↗</strong>
            <strong className="down">−1.6% ↘</strong>
            <span>沪深 300 · AI 策略</span>
          </div>
          <div className="theme-preview-card">
            <p>建议买入优质成长股，控制单笔仓位并设置止损。</p>
            <button type="button" className="btn">
              示例按钮
            </button>
            <div className="theme-preview-status-row">
              <span className="status ok">保存成功</span>
              <span className="status error">数据异常</span>
            </div>
          </div>
        </aside>
      </div>

      {contrastWarnings.length > 0 ? (
        <div className="theme-contrast-warning" role="status">
          对比度提醒：{contrastWarnings.length} 组颜色低于建议阈值，仍可保存。
        </div>
      ) : null}

      <div className="form-actions">
        <button
          type="button"
          className="btn"
          disabled={loading || saving || hasFieldErrors}
          onClick={() => {
            void handleSave()
          }}
        >
          {saving ? '保存中…' : '保存并应用'}
        </button>
        <button type="button" className="btn ghost" disabled={saving} onClick={restoreTemplateDefaults}>
          恢复模板默认值
        </button>
      </div>
    </section>
  )
}
