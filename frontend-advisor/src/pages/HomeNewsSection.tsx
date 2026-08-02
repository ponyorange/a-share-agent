import { useEffect, useRef, useState } from 'react'
import {
  fetchHomeNews,
  fetchHomeNewsBrief,
  refreshHomeNewsBrief,
  type HomeNewsBrief,
  type HomeNewsGroup,
  type HomeNewsResponse,
} from '../api'

const GROUP_LABELS: { key: keyof HomeNewsResponse['groups']; label: string }[] = [
  { key: 'cctv', label: '联播' },
  { key: 'macro', label: '宏观政策' },
  { key: 'index_sentiment', label: '指数情绪' },
  { key: 'sectors', label: '题材热点' },
  { key: 'web', label: '联网舆情' },
]

function visibleGroup(g: HomeNewsGroup | undefined): boolean {
  return Boolean(g && g.items && g.items.length > 0)
}

export function HomeNewsSection() {
  const [news, setNews] = useState<HomeNewsResponse | null>(null)
  const [newsError, setNewsError] = useState<string | null>(null)
  const [newsLoading, setNewsLoading] = useState(true)
  const [brief, setBrief] = useState<HomeNewsBrief | null>(null)
  const [briefError, setBriefError] = useState<string | null>(null)
  const pollRef = useRef<number | null>(null)

  const loadNews = () => {
    setNewsLoading(true)
    setNewsError(null)
    fetchHomeNews()
      .then((d) => setNews(d))
      .catch((e) => setNewsError(e instanceof Error ? e.message : String(e)))
      .finally(() => setNewsLoading(false))
  }

  const loadBrief = () => {
    fetchHomeNewsBrief()
      .then((d) => setBrief(d))
      .catch((e) => setBriefError(e instanceof Error ? e.message : String(e)))
  }

  useEffect(() => {
    loadNews()
    loadBrief()
    return () => {
      if (pollRef.current != null) window.clearInterval(pollRef.current)
    }
  }, [])

  useEffect(() => {
    if (brief?.status !== 'running') {
      if (pollRef.current != null) {
        window.clearInterval(pollRef.current)
        pollRef.current = null
      }
      return
    }
    let ticks = 0
    pollRef.current = window.setInterval(() => {
      ticks += 1
      if (ticks > 45) {
        if (pollRef.current != null) window.clearInterval(pollRef.current)
        pollRef.current = null
        setBriefError('解读生成超时，请稍后重试')
        return
      }
      fetchHomeNewsBrief()
        .then((d) => {
          setBrief(d)
          if (d.status === 'ready') loadNews()
        })
        .catch(() => {})
    }, 2000)
    return () => {
      if (pollRef.current != null) window.clearInterval(pollRef.current)
    }
  }, [brief?.status])

  const onRefresh = async () => {
    setBriefError(null)
    try {
      const d = await refreshHomeNewsBrief()
      setBrief(d)
    } catch (e) {
      setBriefError(e instanceof Error ? e.message : String(e))
    }
  }

  const status = brief?.status || 'idle'
  const refreshing = status === 'running'

  return (
    <section className="home-news" aria-label="今日资讯与解读">
      <div className="home-news-grid">
        <div className="home-tile home-news-pane">
          <div className="home-news-pane-head">
            <h3 className="home-tile-title">今日资讯</h3>
            {news?.as_of ? <span className="meta-line">更新 {news.as_of}</span> : null}
          </div>
          {newsLoading ? <div className="home-tile-skeleton" /> : null}
          {newsError ? (
            <div className="home-tile-error">
              <p>{newsError}</p>
              <button type="button" className="btn ghost" onClick={loadNews}>
                重试
              </button>
            </div>
          ) : null}
          {!newsLoading && news
            ? GROUP_LABELS.filter(({ key }) => visibleGroup(news.groups[key])).map(
                ({ key, label }) => (
                  <div key={key} className="home-news-group">
                    <h4>{label}</h4>
                    <ul className="home-news-list">
                      {news.groups[key].items.slice(0, 6).map((it, i) => (
                        <li key={`${key}-${i}`}>
                          <span>{it.title}</span>
                          {it.summary ? <span className="muted">{it.summary}</span> : null}
                        </li>
                      ))}
                    </ul>
                  </div>
                ),
              )
            : null}
          {!newsLoading &&
          news &&
          !GROUP_LABELS.some(({ key }) => visibleGroup(news.groups[key])) ? (
            <p className="muted">暂无资讯</p>
          ) : null}
        </div>

        <div className="home-tile home-news-pane">
          <div className="home-news-pane-head">
            <h3 className="home-tile-title">Agent 解读</h3>
            <button
              type="button"
              className="btn ghost"
              disabled={refreshing}
              onClick={onRefresh}
            >
              {refreshing ? '生成中…' : '刷新解读'}
            </button>
          </div>
          {briefError ? <p className="home-tile-error">{briefError}</p> : null}
          {status === 'idle' || !brief ? (
            <p className="muted">
              暂无解读。点「刷新解读」生成今日要点与相关板块/股票（会消耗 Token）。
            </p>
          ) : null}
          {status === 'running' ? <p className="muted">正在生成解读…</p> : null}
          {status === 'failed' ? (
            <p className="home-tile-error">{brief?.error || '生成失败'}</p>
          ) : null}
          {status === 'ready' && brief ? (
            <div className="home-news-brief">
              <p className="home-news-summary">{brief.summary}</p>
              {brief.bullets.length ? (
                <ul className="home-news-bullets">
                  {brief.bullets.map((b, i) => (
                    <li key={i}>{b}</li>
                  ))}
                </ul>
              ) : null}
              {brief.sectors.length ? (
                <div className="home-news-chips">
                  {brief.sectors.map((s) => (
                    <span key={s.name} className="home-news-chip" title={s.reason}>
                      {s.name}
                    </span>
                  ))}
                </div>
              ) : null}
              {brief.symbols.length ? (
                <ul className="home-news-symbols">
                  {brief.symbols.map((s) => (
                    <li key={s.symbol}>
                      <span className="mono">
                        {s.symbol} {s.name}
                      </span>
                      <span className="muted">{s.reason}</span>
                    </li>
                  ))}
                </ul>
              ) : null}
            </div>
          ) : null}
        </div>
      </div>
    </section>
  )
}
