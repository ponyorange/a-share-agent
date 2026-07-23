import { useEffect, useRef, useState } from 'react'
import {
  createChart,
  CandlestickSeries,
  LineSeries,
  HistogramSeries,
  ColorType,
  CrosshairMode,
  LineStyle,
  TickMarkType,
  type IChartApi,
  type ISeriesApi,
  type CandlestickData,
  type HistogramData,
  type LineData,
  type Time,
  type UTCTimestamp,
  type SeriesType,
  type BusinessDay,
} from 'lightweight-charts'
import {
  priceDecimals,
  computeSma,
  DAILY_MA_COLORS,
  type KlineBar,
  type KlineRange,
  type KlineResponse,
} from '../klineApi'

export type HoverBar = {
  time: string
  open: number
  high: number
  low: number
  close: number
  volume?: number
  ma5?: number | null
  ma10?: number | null
  ma20?: number | null
}

type Props = {
  data: KlineResponse | null
  onHover?: (bar: HoverBar | null) => void
}

function toChartTime(raw: string, asUnix: boolean): Time {
  if (/^\d{4}-\d{2}-\d{2}$/.test(raw)) {
    return raw as Time
  }
  const normalized = raw.includes('T') ? raw : raw.replace(' ', 'T')
  const withSec = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(normalized)
    ? `${normalized}:00`
    : normalized
  const ms = Date.parse(withSec)
  if (!Number.isNaN(ms) && asUnix) {
    return Math.floor(ms / 1000) as UTCTimestamp
  }
  return raw.slice(0, 10) as Time
}

function pad2(n: number) {
  return String(n).padStart(2, '0')
}

function formatTickMark(time: Time, tickMarkType: TickMarkType, range: KlineRange): string {
  if (typeof time === 'string') {
    // business day YYYY-MM-DD
    const [y, m, d] = time.split('-')
    if (tickMarkType === TickMarkType.Year) return y
    if (tickMarkType === TickMarkType.Month) return `${y}-${m}`
    return `${m}-${d}`
  }
  if (typeof time === 'object' && time !== null && 'year' in time) {
    const bd = time as BusinessDay
    if (tickMarkType === TickMarkType.Year) return String(bd.year)
    if (tickMarkType === TickMarkType.Month) {
      return `${bd.year}-${pad2(bd.month)}`
    }
    return `${pad2(bd.month)}-${pad2(bd.day)}`
  }
  const ts = typeof time === 'number' ? time : 0
  const date = new Date(ts * 1000)
  if (range === 'realtime' || range === '5d') {
    if (tickMarkType === TickMarkType.Time || tickMarkType === TickMarkType.TimeWithSeconds) {
      return `${pad2(date.getHours())}:${pad2(date.getMinutes())}`
    }
    return `${pad2(date.getMonth() + 1)}-${pad2(date.getDate())}`
  }
  return `${pad2(date.getMonth() + 1)}-${pad2(date.getDate())}`
}

function clearSeries(chart: IChartApi, seriesList: ISeriesApi<SeriesType>[]) {
  for (const s of seriesList) {
    try {
      chart.removeSeries(s)
    } catch {
      /* already removed */
    }
  }
  seriesList.length = 0
}

/** Show recent bars across the full plot width (no empty left band). */
function layoutViewport(
  chart: IChartApi,
  bars: KlineBar[],
  range: KlineRange,
  containerWidth: number,
) {
  const total = bars.length
  if (total <= 0) return

  const useUnix = range === 'realtime' || range === '5d'
  const plotWidth = Math.max(240, containerWidth - 72)
  const spacingHint: Record<KlineRange, number> = {
    realtime: 3,
    '5d': 4,
    daily: 8,
    weekly: 9,
    monthly: 11,
  }
  const hint = spacingHint[range] ?? 8

  chart.timeScale().applyOptions({
    rightOffset: 2,
    fixRightEdge: false,
    minBarSpacing: 2,
    borderVisible: true,
    barSpacing: hint,
  })

  if (useUnix) {
    chart.timeScale().fitContent()
    return
  }

  // Use real dates — logical indices were leaving a huge blank region on the left
  const visible = Math.min(total, Math.max(60, Math.floor(plotWidth / hint)))
  const slice = bars.slice(-visible)
  const from = toChartTime(slice[0].time, false)
  const to = toChartTime(slice[slice.length - 1].time, false)
  try {
    chart.timeScale().setVisibleRange({ from, to })
  } catch {
    chart.timeScale().setVisibleLogicalRange({
      from: total - visible,
      to: total - 1,
    })
  }
}

export function KlineChart({ data, onHover }: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const seriesRef = useRef<ISeriesApi<SeriesType>[]>([])
  const rangeRef = useRef<KlineRange>('daily')
  const decimalsRef = useRef(2)
  const barsByTimeRef = useRef<Map<string, HoverBar>>(new Map())
  const barsRef = useRef<KlineBar[]>([])
  const onHoverRef = useRef(onHover)
  const lastHoverKeyRef = useRef<string | null>(null)
  const sizeRef = useRef({ width: 0, height: 0 })
  const [chartError, setChartError] = useState<string | null>(null)

  useEffect(() => {
    onHoverRef.current = onHover
  }, [onHover])

  useEffect(() => {
    const el = containerRef.current
    if (!el) return

    const initialWidth = Math.max(el.clientWidth, el.getBoundingClientRect().width, 320)
    const initialHeight = Math.max(el.clientHeight, el.getBoundingClientRect().height, 420)

    const chart = createChart(el, {
      width: initialWidth,
      height: initialHeight,
      layout: {
        background: { type: ColorType.Solid, color: '#0b1715' },
        textColor: '#9ec4bb',
        fontFamily: "'IBM Plex Sans', sans-serif",
        fontSize: 12,
        attributionLogo: false,
      },
      grid: {
        vertLines: { color: 'rgba(125, 200, 184, 0.07)' },
        horzLines: { color: 'rgba(125, 200, 184, 0.07)' },
      },
      crosshair: {
        mode: CrosshairMode.Magnet,
        vertLine: {
          color: 'rgba(61, 207, 182, 0.55)',
          width: 1,
          style: LineStyle.Dashed,
          labelBackgroundColor: '#1a3d37',
        },
        horzLine: {
          color: 'rgba(61, 207, 182, 0.55)',
          width: 1,
          style: LineStyle.Dashed,
          labelBackgroundColor: '#1a3d37',
        },
      },
      leftPriceScale: {
        visible: true,
        borderVisible: true,
        borderColor: 'rgba(125, 200, 184, 0.22)',
        minimumWidth: 64,
        scaleMargins: { top: 0.08, bottom: 0.22 },
      },
      rightPriceScale: {
        visible: false,
        borderVisible: false,
      },
      timeScale: {
        borderColor: 'rgba(125, 200, 184, 0.22)',
        timeVisible: false,
        secondsVisible: false,
        rightOffset: 3,
        barSpacing: 9,
        minBarSpacing: 3,
        fixRightEdge: true,
        lockVisibleTimeRangeOnResize: false,
        tickMarkFormatter: (time: Time, tickMarkType: TickMarkType) =>
          formatTickMark(time, tickMarkType, rangeRef.current),
      },
      localization: {
        locale: 'zh-CN',
        dateFormat: 'yyyy-MM-dd',
        priceFormatter: (price: number) => price.toFixed(decimalsRef.current),
      },
      handleScroll: {
        mouseWheel: true,
        pressedMouseMove: true,
        horzTouchDrag: true,
        vertTouchDrag: false,
      },
      handleScale: {
        axisPressedMouseMove: { time: true, price: true },
        mouseWheel: true,
        pinch: true,
      },
    })
    chartRef.current = chart
    // Debug handle for browser inspection
    ;(window as unknown as { __klineChart?: IChartApi }).__klineChart = chart
    sizeRef.current = { width: initialWidth, height: initialHeight }

    const syncSize = (relayout = false) => {
      const rect = el.getBoundingClientRect()
      const width = Math.floor(rect.width || el.clientWidth)
      const height = Math.floor(rect.height || el.clientHeight)
      if (width < 40 || height < 40) return
      const prevWidth = sizeRef.current.width
      const changed =
        Math.abs(width - prevWidth) >= 2 ||
        Math.abs(height - sizeRef.current.height) >= 2
      if (changed) {
        sizeRef.current = { width, height }
        chart.applyOptions({ width, height })
      }
      // Relayout only on first paint or large width jumps (keep user zoom otherwise)
      if (relayout || Math.abs(width - prevWidth) >= 48) {
        if (barsRef.current.length > 0) {
          layoutViewport(chart, barsRef.current, rangeRef.current, width)
        }
      }
    }

    const ro = new ResizeObserver(() => {
      window.requestAnimationFrame(() => syncSize(false))
    })
    ro.observe(el)
    window.requestAnimationFrame(() => syncSize(true))

    chart.subscribeCrosshairMove((param) => {
      if (!param.time || !param.point) {
        if (lastHoverKeyRef.current !== null) {
          lastHoverKeyRef.current = null
          onHoverRef.current?.(null)
        }
        return
      }
      let key = ''
      if (typeof param.time === 'string') {
        key = param.time
      } else if (typeof param.time === 'number') {
        key = String(param.time)
      } else if (param.time && typeof param.time === 'object') {
        const bd = param.time as BusinessDay
        key = `${bd.year}-${pad2(bd.month)}-${pad2(bd.day)}`
      }
      if (key === lastHoverKeyRef.current) return
      lastHoverKeyRef.current = key
      onHoverRef.current?.(barsByTimeRef.current.get(key) ?? null)
    })

    return () => {
      ro.disconnect()
      clearSeries(chart, seriesRef.current)
      chart.remove()
      chartRef.current = null
    }
  }, [])

  useEffect(() => {
    const chart = chartRef.current
    if (!chart || !data) return

    try {
      setChartError(null)
      clearSeries(chart, seriesRef.current)
      rangeRef.current = data.range
      const decimals = priceDecimals(data.symbol)
      decimalsRef.current = decimals
      const priceFormat = {
        type: 'price' as const,
        precision: decimals,
        minMove: decimals === 3 ? 0.001 : 0.01,
      }

      const useUnix = data.range === 'realtime' || data.range === '5d'
      const el = containerRef.current
      if (el) {
        const rect = el.getBoundingClientRect()
        const width = Math.floor(rect.width || el.clientWidth)
        const height = Math.floor(rect.height || el.clientHeight)
        if (width > 40 && height > 40) {
          sizeRef.current = { width, height }
          chart.applyOptions({ width, height })
        }
      }

      chart.applyOptions({
        timeScale: {
          timeVisible: useUnix,
          secondsVisible: false,
          rightOffset: 2,
          fixRightEdge: true,
        },
        localization: {
          priceFormatter: (price: number) => price.toFixed(decimals),
        },
      })

      const bars = data.bars
      barsRef.current = bars
      const isLine = data.chart_type === 'line' || data.range === 'realtime'
      const showDailyMa = data.range === 'daily' && !isLine
      const ma5Vals = showDailyMa ? computeSma(bars, 5) : []
      const ma10Vals = showDailyMa ? computeSma(bars, 10) : []
      const ma20Vals = showDailyMa ? computeSma(bars, 20) : []
      const lookup = new Map<string, HoverBar>()
      for (let i = 0; i < bars.length; i++) {
        const b = bars[i]
        const t = toChartTime(b.time, data.range === 'realtime' || data.range === '5d')
        const key =
          typeof t === 'number'
            ? String(t)
            : typeof t === 'string'
              ? t
              : `${(t as BusinessDay).year}-${pad2((t as BusinessDay).month)}-${pad2((t as BusinessDay).day)}`
        lookup.set(key, {
          time: b.time,
          open: b.open,
          high: b.high,
          low: b.low,
          close: b.close,
          volume: b.volume,
          ma5: showDailyMa ? ma5Vals[i] : undefined,
          ma10: showDailyMa ? ma10Vals[i] : undefined,
          ma20: showDailyMa ? ma20Vals[i] : undefined,
        })
      }
      barsByTimeRef.current = lookup

      if (isLine) {
        const series = chart.addSeries(LineSeries, {
          color: '#3dcfb6',
          lineWidth: 2,
          lastValueVisible: true,
          priceLineVisible: true,
          priceScaleId: 'left',
          crosshairMarkerVisible: true,
          crosshairMarkerRadius: 4,
          priceFormat,
        })
        seriesRef.current.push(series)
        series.setData(
          bars.map((b: KlineBar) => ({
            time: toChartTime(b.time, useUnix),
            value: b.close,
          })) as LineData[],
        )
        if (data.pre_close != null) {
          series.createPriceLine({
            price: data.pre_close,
            color: 'rgba(240, 194, 122, 0.8)',
            lineWidth: 1,
            lineStyle: LineStyle.Dashed,
            axisLabelVisible: true,
            title: '昨收',
          })
        }
      } else {
        const series = chart.addSeries(CandlestickSeries, {
          upColor: '#ef5350',
          downColor: '#26a69a',
          borderUpColor: '#ef5350',
          borderDownColor: '#26a69a',
          wickUpColor: '#ef5350',
          wickDownColor: '#26a69a',
          borderVisible: true,
          priceScaleId: 'left',
          lastValueVisible: true,
          priceLineVisible: true,
          priceFormat,
        })
        seriesRef.current.push(series)
        series.setData(
          bars.map((b) => ({
            time: toChartTime(b.time, useUnix),
            open: b.open,
            high: b.high,
            low: b.low,
            close: b.close,
          })) as CandlestickData[],
        )

        if (data.range === 'daily') {
          const maSpecs: { key: 'ma5' | 'ma10' | 'ma20'; period: number; color: string; title: string }[] = [
            { key: 'ma5', period: 5, color: DAILY_MA_COLORS.ma5, title: 'MA5' },
            { key: 'ma10', period: 10, color: DAILY_MA_COLORS.ma10, title: 'MA10' },
            { key: 'ma20', period: 20, color: DAILY_MA_COLORS.ma20, title: 'MA20' },
          ]
          const maSeriesValues = { ma5: ma5Vals, ma10: ma10Vals, ma20: ma20Vals }
          for (const spec of maSpecs) {
            const values = maSeriesValues[spec.key]
            const points: LineData[] = []
            for (let i = 0; i < bars.length; i++) {
              const v = values[i]
              if (v == null) continue
              points.push({
                time: toChartTime(bars[i].time, false),
                value: v,
              })
            }
            if (points.length === 0) continue
            const maSeries = chart.addSeries(LineSeries, {
              color: spec.color,
              lineWidth: 1,
              lastValueVisible: true,
              priceLineVisible: false,
              priceScaleId: 'left',
              crosshairMarkerVisible: false,
              title: spec.title,
              priceFormat,
            })
            seriesRef.current.push(maSeries)
            maSeries.setData(points)
          }
        }
      }

      if (bars.some((b) => b.volume != null)) {
        const volumeSeries = chart.addSeries(HistogramSeries, {
          priceFormat: { type: 'volume' },
          priceScaleId: 'volume',
          lastValueVisible: false,
          priceLineVisible: false,
        })
        seriesRef.current.push(volumeSeries)
        chart.priceScale('volume').applyOptions({
          scaleMargins: { top: 0.78, bottom: 0 },
        })
        chart.priceScale('left').applyOptions({
          scaleMargins: { top: 0.06, bottom: 0.24 },
        })
        volumeSeries.setData(
          bars.map((b) => ({
            time: toChartTime(b.time, useUnix),
            value: b.volume ?? 0,
            color:
              b.close >= b.open
                ? 'rgba(239, 83, 80, 0.45)'
                : 'rgba(38, 166, 154, 0.45)',
          })) as HistogramData[],
        )
      } else {
        chart.priceScale('left').applyOptions({
          scaleMargins: { top: 0.06, bottom: 0.05 },
        })
      }

      chart.priceScale('left').applyOptions({
        visible: true,
        autoScale: true,
        minimumWidth: 64,
        borderVisible: true,
      })

      const width = sizeRef.current.width || containerRef.current?.clientWidth || 800
      // setData is async internally — apply viewport on next frames
      requestAnimationFrame(() => {
        layoutViewport(chart, bars, data.range, width)
        requestAnimationFrame(() => {
          layoutViewport(chart, bars, data.range, width)
        })
      })
    } catch (err) {
      setChartError(err instanceof Error ? err.message : String(err))
    }
  }, [data])

  return (
    <div className="kline-chart-wrap">
      {chartError ? <div className="error-banner">{chartError}</div> : null}
      <div className="kline-chart" ref={containerRef} />
    </div>
  )
}
