import { useEffect, useRef, useState } from 'react'
import {
  CandlestickSeries,
  createChart,
  type IChartApi,
  type ISeriesApi,
} from 'lightweight-charts'
import { fetchAdvisorKline } from '../api'

export default function PaperTraderChart({ symbol }: { symbol: string | null }) {
  const hostRef = useRef<HTMLDivElement | null>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const seriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    const el = hostRef.current
    if (!el) return
    const chart = createChart(el, {
      height: 280,
      layout: {
        background: { color: 'transparent' },
        textColor: '#9aa4b2',
      },
      grid: {
        vertLines: { color: 'rgba(127,127,127,0.15)' },
        horzLines: { color: 'rgba(127,127,127,0.15)' },
      },
      rightPriceScale: { borderVisible: false },
      timeScale: { borderVisible: false },
    })
    const series = chart.addSeries(CandlestickSeries, {
      upColor: '#d64545',
      downColor: '#1a7f37',
      borderVisible: false,
      wickUpColor: '#d64545',
      wickDownColor: '#1a7f37',
    })
    chartRef.current = chart
    seriesRef.current = series
    const onResize = () => {
      if (hostRef.current) {
        chart.applyOptions({ width: hostRef.current.clientWidth })
      }
    }
    onResize()
    window.addEventListener('resize', onResize)
    return () => {
      window.removeEventListener('resize', onResize)
      chart.remove()
      chartRef.current = null
      seriesRef.current = null
    }
  }, [])

  useEffect(() => {
    if (!symbol || !seriesRef.current) {
      seriesRef.current?.setData([])
      return
    }
    let cancelled = false
    setLoading(true)
    setError(null)
    fetchAdvisorKline(symbol, 'daily')
      .then((payload) => {
        if (cancelled) return
        const bars = Array.isArray(payload.bars) ? payload.bars : []
        const data = bars
          .map((b) => {
            const time = String(b.time || '').slice(0, 10)
            const open = Number(b.open)
            const high = Number(b.high)
            const low = Number(b.low)
            const close = Number(b.close)
            if (!time || [open, high, low, close].some((x) => Number.isNaN(x))) {
              return null
            }
            return { time, open, high, low, close }
          })
          .filter(Boolean) as Array<{
          time: string
          open: number
          high: number
          low: number
          close: number
        }>
        seriesRef.current?.setData(data as never)
        chartRef.current?.timeScale().fitContent()
        if (!data.length) setError('无 K 线数据')
      })
      .catch((err: Error) => {
        if (!cancelled) setError(err.message)
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [symbol])

  if (!symbol) {
    return <p className="status">选择左侧标的查看日 K</p>
  }

  return (
    <div className="paper-trader-chart">
      {loading ? <p className="status">K 线加载中…</p> : null}
      {error ? <p className="status error">{error}</p> : null}
      <div ref={hostRef} className="paper-trader-chart-host" />
    </div>
  )
}
