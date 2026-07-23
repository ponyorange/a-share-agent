import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Navigate, useParams } from 'react-router-dom'
import {
  fetchData,
  getCategories,
  getInterface,
  getInterfaces,
  getSourceHealth,
  type Category,
  type FetchResult,
  type InterfaceDetail,
  type InterfaceSummary,
} from './api'
import { CategoryNav } from './components/CategoryNav'
import { DataTable } from './components/DataTable'
import { InterfaceList } from './components/InterfaceList'
import { PageNav } from './components/PageNav'
import { ParamForm } from './components/ParamForm'
import { useSources } from './hooks/useSources'
import { DEFAULT_SOURCE } from './sources'
import { readUrlState, writeUrlState } from './urlState'

function toFormValues(
  detail: InterfaceDetail,
  overrides?: Record<string, string>,
): Record<string, string> {
  const values: Record<string, string> = {}
  const example = detail.example_params || {}
  for (const p of detail.params) {
    if (p.name in example && example[p.name] !== undefined && example[p.name] !== null) {
      values[p.name] = String(example[p.name])
    } else if (p.default !== null && p.default !== undefined) {
      values[p.name] = String(p.default)
    } else {
      values[p.name] = ''
    }
  }
  for (const [k, v] of Object.entries(example)) {
    if (!(k in values) && v !== undefined && v !== null) {
      values[k] = String(v)
    }
  }
  if (overrides) {
    for (const [k, v] of Object.entries(overrides)) {
      values[k] = v
    }
  }
  return values
}

function parseParamValues(
  detail: InterfaceDetail,
  values: Record<string, string>,
): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  const known = new Set(detail.params.map((p) => p.name))

  for (const [key, raw] of Object.entries(values)) {
    if (!known.has(key) && raw === '') continue
    const meta = detail.params.find((p) => p.name === key)
    if (raw === '' && meta && !meta.required) {
      if (!(key in detail.example_params)) continue
      if (detail.example_params[key] !== '') continue
    }
    if (raw === '') {
      out[key] = ''
      continue
    }

    const anno = (meta?.annotation || '').toLowerCase()

    // Stock codes like 000001 / 00700 must stay strings (Number would become 1)
    if (anno === 'str' || anno.includes('str')) {
      out[key] = raw
      continue
    }
    if (/^0\d+$/.test(raw)) {
      out[key] = raw
      continue
    }

    if (anno === 'bool' || anno.includes('bool')) {
      out[key] = raw === 'true' || raw === '1'
      continue
    }
    if (raw === 'true' || raw === 'false') {
      out[key] = raw === 'true'
      continue
    }

    const wantNumber =
      anno === 'int' ||
      anno === 'float' ||
      anno.includes('int') ||
      anno.includes('float') ||
      key === 'timeout' ||
      key === 'limit'

    if (wantNumber && /^-?\d+(\.\d+)?$/.test(raw)) {
      out[key] = Number(raw)
      continue
    }

    out[key] = raw
  }
  return out
}

export default function App() {
  const params = useParams()
  const source = (params.source || DEFAULT_SOURCE).toLowerCase()
  const sources = useSources()
  const sourceMeta = sources.find((s) => s.id === source)

  const [version, setVersion] = useState('—')
  const [interfaceCount, setInterfaceCount] = useState(0)
  const [categories, setCategories] = useState<Category[]>([])
  const [total, setTotal] = useState(0)
  const [category, setCategory] = useState<string | null>(null)
  const [keyword, setKeyword] = useState('')
  const [debouncedKeyword, setDebouncedKeyword] = useState('')
  const [interfaces, setInterfaces] = useState<InterfaceSummary[]>([])
  const [listLoading, setListLoading] = useState(true)
  const [selected, setSelected] = useState<string | null>(null)
  const [detail, setDetail] = useState<InterfaceDetail | null>(null)
  const [paramValues, setParamValues] = useState<Record<string, string>>({})
  const [result, setResult] = useState<FetchResult | null>(null)
  const [fetching, setFetching] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [view, setView] = useState<'table' | 'json'>('table')
  const urlReady = useRef(false)
  const paramSyncTimer = useRef<number | null>(null)
  const sourceReady = sources.some((s) => s.id === source)

  useEffect(() => {
    const state = readUrlState()
    setCategory(state.category)
    setKeyword(state.q)
    setDebouncedKeyword(state.q)
    setSelected(state.api)
    setDetail(null)
    setResult(null)
    setParamValues({})
    setError(null)
    urlReady.current = false
  }, [source])

  useEffect(() => {
    getSourceHealth(source)
      .then((h) => {
        setVersion(h.version || '—')
        setInterfaceCount(h.interface_count || 0)
      })
      .catch(() => {
        setVersion('offline')
      })
    getCategories(source)
      .then((data) => {
        setCategories(data.categories)
        setTotal(data.total)
      })
      .catch((err) => setError(String(err.message || err)))
  }, [source])

  useEffect(() => {
    const t = window.setTimeout(() => setDebouncedKeyword(keyword.trim()), 250)
    return () => window.clearTimeout(t)
  }, [keyword])

  useEffect(() => {
    let cancelled = false
    setListLoading(true)
    getInterfaces(source, category, debouncedKeyword || undefined)
      .then((data) => {
        if (!cancelled) setInterfaces(data.interfaces)
      })
      .catch((err) => {
        if (!cancelled) setError(String(err.message || err))
      })
      .finally(() => {
        if (!cancelled) setListLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [source, category, debouncedKeyword])

  const selectInterface = useCallback(
    async (name: string, overrides?: Record<string, string>) => {
      setSelected(name)
      setError(null)
      setResult(null)
      try {
        const d = await getInterface(source, name)
        setDetail(d)
        setParamValues(toFormValues(d, overrides))
      } catch (err) {
        setDetail(null)
        setError(err instanceof Error ? err.message : String(err))
      } finally {
        urlReady.current = true
      }
    },
    [source],
  )

  useEffect(() => {
    const state = readUrlState()
    if (state.api) {
      void selectInterface(state.api, state.params)
    } else {
      urlReady.current = true
    }
  }, [source, selectInterface])

  useEffect(() => {
    if (!urlReady.current) return

    if (paramSyncTimer.current !== null) {
      window.clearTimeout(paramSyncTimer.current)
    }
    paramSyncTimer.current = window.setTimeout(() => {
      writeUrlState({
        api: selected,
        category,
        q: keyword,
        params: paramValues,
      })
    }, 150)

    return () => {
      if (paramSyncTimer.current !== null) {
        window.clearTimeout(paramSyncTimer.current)
      }
    }
  }, [selected, category, keyword, paramValues])

  useEffect(() => {
    const onPopState = () => {
      const state = readUrlState()
      setCategory(state.category)
      setKeyword(state.q)
      if (state.api) {
        void selectInterface(state.api, state.params)
      } else {
        setSelected(null)
        setDetail(null)
        setParamValues({})
        setResult(null)
      }
    }
    window.addEventListener('popstate', onPopState)
    return () => window.removeEventListener('popstate', onPopState)
  }, [selectInterface])

  const onParamChange = useCallback((name: string, value: string) => {
    setParamValues((prev) => ({ ...prev, [name]: value }))
  }, [])

  const onFetch = useCallback(async () => {
    if (!detail) return
    writeUrlState({
      api: detail.name,
      category,
      q: keyword,
      params: paramValues,
    })
    setFetching(true)
    setError(null)
    try {
      const parsed = parseParamValues(detail, paramValues)
      const data = await fetchData(source, detail.name, parsed)
      setResult(data)
      setView('table')
    } catch (err) {
      setResult(null)
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setFetching(false)
    }
  }, [detail, paramValues, category, keyword, source])

  const metaLine = useMemo(() => {
    if (!result) return null
    const parts = [
      `类型 ${result.type}`,
      `共 ${result.total} 行`,
      `展示 ${result.returned} 行`,
    ]
    if (result.truncated) parts.push('已截断')
    return parts.join(' · ')
  }, [result])

  if (!sourceReady && sources.length > 0) {
    return <Navigate to={`/${DEFAULT_SOURCE}`} replace />
  }

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark" aria-hidden />
          <div>
            <h1>{sourceMeta?.label || source} Explorer</h1>
            <p className="brand-sub">
              多数据源接口浏览器
              {sourceMeta?.docs_url ? (
                <>
                  {' · '}
                  <a href={sourceMeta.docs_url} target="_blank" rel="noreferrer">
                    文档
                  </a>
                </>
              ) : null}
            </p>
          </div>
        </div>
        <div className="top-meta">
          <PageNav sources={sources} activeFeature="explorer" />
          <label className="search">
            <span className="sr-only">搜索接口</span>
            <input
              type="search"
              placeholder="搜索接口名或说明…"
              value={keyword}
              onChange={(e) => setKeyword(e.target.value)}
            />
          </label>
          <div className="version-chip">
            <span>
              {source} {version}
            </span>
            <span>{interfaceCount || total} 接口</span>
          </div>
        </div>
      </header>

      {sourceMeta && sourceMeta.ready === false && sourceMeta.message ? (
        <div className="error-banner source-banner">{sourceMeta.message}</div>
      ) : null}

      <div className="layout">
        <aside className="sidebar">
          <CategoryNav
            categories={categories}
            active={category}
            onSelect={setCategory}
            total={total}
          />
          <InterfaceList
            items={interfaces}
            active={selected}
            onSelect={(name) => {
              void selectInterface(name)
            }}
            loading={listLoading}
          />
        </aside>

        <main className="main">
          {!detail ? (
            <section className="welcome">
              <h2>选择一个接口开始</h2>
              <p>
                当前数据源：<strong>{sourceMeta?.label || source}</strong>
                。左侧浏览接口目录，选中后填写参数并拉取预览。地址栏会同步当前接口与参数，便于分享。
              </p>
            </section>
          ) : (
            <>
              <section className="detail-head">
                <div>
                  <p className="crumb">
                    {detail.category_label}
                    <span>/</span>
                    <code>{detail.name}</code>
                  </p>
                  <h2>{detail.doc || detail.name}</h2>
                </div>
                <button
                  type="button"
                  className="btn-primary"
                  disabled={fetching}
                  onClick={onFetch}
                >
                  {fetching ? '拉取中…' : '拉取数据'}
                </button>
              </section>

              <section className="panel">
                <h3>参数</h3>
                <ParamForm
                  params={detail.params}
                  values={paramValues}
                  onChange={onParamChange}
                />
              </section>

              {detail.docstring ? (
                <section className="panel doc-panel">
                  <h3>说明</h3>
                  <pre className="docstring">{detail.docstring}</pre>
                </section>
              ) : null}

              {error ? <div className="error-banner">{error}</div> : null}

              <section className="panel result-panel">
                <div className="result-head">
                  <h3>结果</h3>
                  <div className="result-actions">
                    {metaLine ? <span className="meta-line">{metaLine}</span> : null}
                    <div className="view-toggle" role="group" aria-label="视图">
                      <button
                        type="button"
                        className={view === 'table' ? 'active' : ''}
                        onClick={() => setView('table')}
                      >
                        表格
                      </button>
                      <button
                        type="button"
                        className={view === 'json' ? 'active' : ''}
                        onClick={() => setView('json')}
                      >
                        JSON
                      </button>
                    </div>
                  </div>
                </div>
                <DataTable result={result} view={view} />
              </section>
            </>
          )}
        </main>
      </div>
    </div>
  )
}
